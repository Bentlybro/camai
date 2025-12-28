"""Notification system - Discord, MQTT, file logging."""
import json
import logging
import time
import threading
from pathlib import Path
from datetime import datetime
from queue import Queue, Empty
from typing import Optional
import numpy as np

from events import Event

logger = logging.getLogger(__name__)


class NotificationManager:
    """Async notification delivery."""

    def __init__(self):
        self._handlers = []
        self._queue = Queue()
        self._running = False
        self._thread = None
        self._file_logger = None

    def add_file_logger(self, log_dir: str = "logs", snapshot_dir: str = "snapshots"):
        """Add file logging handler."""
        self._file_logger = FileLogger(log_dir, snapshot_dir)
        self._handlers.append(self._file_logger)

    def get_snapshot_path(self, event: 'Event', frame) -> Optional[str]:
        """Get the snapshot path that would be saved for an event."""
        if self._file_logger and frame is not None:
            return self._file_logger.get_snapshot_path(event)
        return None

    def add_discord(self, webhook_url: str):
        """Add Discord webhook handler."""
        self._handlers.append(DiscordHandler(webhook_url))

    def add_mqtt(self, broker: str, port: int = 1883, topic: str = "camai/events"):
        """Add MQTT handler."""
        self._handlers.append(MQTTHandler(broker, port, topic))

    def start(self):
        """Start notification worker."""
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop notification worker."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self, event: Event, snapshot: np.ndarray = None):
        """Queue notification."""
        snap_copy = snapshot.copy() if snapshot is not None else None
        self._queue.put((event, snap_copy))

    def _worker(self):
        while self._running:
            try:
                event, snapshot = self._queue.get(timeout=1)
                for handler in self._handlers:
                    try:
                        handler.send(event, snapshot)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
            except Empty:
                continue


class FileLogger:
    """Log events to JSON lines file."""

    def __init__(self, log_dir: str, snapshot_dir: str, retention_days: int = 7):
        self.log_dir = Path(log_dir)
        self.snapshot_dir = Path(snapshot_dir)
        self.retention_days = retention_days
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_snapshot_path = None
        self._last_cleanup = 0

    def get_snapshot_path(self, event: Event) -> str:
        """Get the API path for a snapshot."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{event.event_type.value}_{ts}.jpg"
        return f"/api/snapshots/{filename}"

    def send(self, event: Event, snapshot: np.ndarray = None):
        import cv2

        entry = event.to_dict()
        entry["timestamp_iso"] = datetime.fromtimestamp(event.timestamp).isoformat()

        # Save snapshot
        if snapshot is not None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{event.event_type.value}_{ts}.jpg"
            path = self.snapshot_dir / filename
            cv2.imwrite(str(path), snapshot)
            entry["snapshot"] = str(path)
            self._last_snapshot_path = f"/api/snapshots/{filename}"
        else:
            self._last_snapshot_path = None

        # Append to log
        log_file = self.log_dir / f"events_{datetime.now():%Y-%m-%d}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Run cleanup periodically (every hour)
        now = time.time()
        if now - self._last_cleanup > 3600:
            self.cleanup_old_files()
            self._last_cleanup = now

    def cleanup_old_files(self):
        """Delete snapshots and logs older than retention_days."""
        cutoff = time.time() - (self.retention_days * 86400)
        deleted_snapshots = 0
        deleted_logs = 0

        # Clean up old snapshots
        if self.snapshot_dir.exists():
            for f in self.snapshot_dir.glob("*.jpg"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted_snapshots += 1
                except Exception as e:
                    logger.debug(f"Failed to delete snapshot {f}: {e}")

        # Clean up old log files
        if self.log_dir.exists():
            for f in self.log_dir.glob("events_*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted_logs += 1
                except Exception as e:
                    logger.debug(f"Failed to delete log {f}: {e}")

        if deleted_snapshots > 0 or deleted_logs > 0:
            logger.info(f"Cleanup: deleted {deleted_snapshots} snapshots, {deleted_logs} logs older than {self.retention_days} days")


class DiscordHandler:
    """Send notifications to Discord."""

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, event: Event, snapshot: np.ndarray = None):
        import requests
        import cv2
        import io

        colors = {
            "person_detected": 0x3498db,
            "person_dwelling": 0xe74c3c,
            "vehicle_detected": 0xf39c12,
            "vehicle_stopped": 0xe74c3c,
            "package_detected": 0x9b59b6,
        }

        embed = {
            "title": event.event_type.value.replace("_", " ").title(),
            "color": colors.get(event.event_type.value, 0x95a5a6),
            "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
            "fields": [{"name": "Detection", "value": f"{event.class_name} ({event.confidence:.0%})", "inline": True}]
        }

        if snapshot is not None:
            _, buf = cv2.imencode('.jpg', snapshot)
            files = {"file": ("snapshot.jpg", io.BytesIO(buf.tobytes()), "image/jpeg")}
            embed["image"] = {"url": "attachment://snapshot.jpg"}
            requests.post(self.url, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=10)
        else:
            requests.post(self.url, json={"embeds": [embed]}, timeout=5)


class MQTTHandler:
    """Publish events to MQTT."""

    def __init__(self, broker: str, port: int, topic: str):
        self.topic = topic
        try:
            import paho.mqtt.client as mqtt
            self.client = mqtt.Client()
            self.client.connect(broker, port, 60)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"MQTT connect failed: {e}")
            self.client = None

    def send(self, event: Event, snapshot: np.ndarray = None):
        if self.client:
            self.client.publish(f"{self.topic}/{event.event_type.value}", json.dumps(event.to_dict()))
