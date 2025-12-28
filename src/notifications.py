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
import cv2

from events import Event

logger = logging.getLogger(__name__)


def annotate_snapshot(frame: np.ndarray, event: Event) -> np.ndarray:
    """Draw bounding box and label on snapshot."""
    if frame is None:
        return frame

    annotated = frame.copy()
    bbox = event.bbox

    if not bbox or len(bbox) < 4:
        return annotated

    x1, y1, x2, y2 = [int(c) for c in bbox]

    # Choose color based on event type
    colors = {
        "person": (0, 255, 0),      # Green
        "car": (255, 165, 0),        # Orange
        "truck": (255, 165, 0),      # Orange
        "package": (255, 0, 255),    # Magenta
    }
    color = colors.get(event.class_name, (0, 255, 255))  # Default yellow

    # Draw bounding box
    cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)

    # Build label
    label = event.description if event.description else event.class_name
    conf_text = f"{event.confidence:.0%}"
    full_label = f"{label} {conf_text}"

    # Draw label background
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.7
    thickness = 2
    (text_w, text_h), baseline = cv2.getTextSize(full_label, font, font_scale, thickness)

    # Position label above box, or below if near top
    label_y = y1 - 10 if y1 > 30 else y2 + text_h + 10
    label_x = x1

    # Draw background rectangle for text
    cv2.rectangle(annotated,
                  (label_x, label_y - text_h - 5),
                  (label_x + text_w + 10, label_y + 5),
                  color, -1)

    # Draw text
    cv2.putText(annotated, full_label, (label_x + 5, label_y),
                font, font_scale, (0, 0, 0), thickness)

    return annotated


class NotificationManager:
    """Async notification delivery."""

    def __init__(self):
        self._handlers = []
        self._queue = Queue()
        self._running = False
        self._thread = None
        self._file_logger = None
        self._discord_handler = None
        self._mqtt_handler = None

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
        # Remove existing Discord handler if any
        self.remove_discord()
        handler = DiscordHandler(webhook_url)
        self._handlers.append(handler)
        self._discord_handler = handler

    def remove_discord(self):
        """Remove Discord handler."""
        if hasattr(self, '_discord_handler') and self._discord_handler:
            if self._discord_handler in self._handlers:
                self._handlers.remove(self._discord_handler)
            self._discord_handler = None

    def has_discord(self) -> bool:
        """Check if Discord handler is active."""
        return hasattr(self, '_discord_handler') and self._discord_handler is not None

    def add_mqtt(self, broker: str, port: int = 1883, topic: str = "camai/events"):
        """Add MQTT handler."""
        # Remove existing MQTT handler if any
        self.remove_mqtt()
        handler = MQTTHandler(broker, port, topic)
        self._handlers.append(handler)
        self._mqtt_handler = handler

    def remove_mqtt(self):
        """Remove MQTT handler."""
        if self._mqtt_handler:
            if self._mqtt_handler in self._handlers:
                self._handlers.remove(self._mqtt_handler)
            self._mqtt_handler = None

    def has_mqtt(self) -> bool:
        """Check if MQTT handler is active."""
        return self._mqtt_handler is not None

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
        entry = event.to_dict()
        entry["timestamp_iso"] = datetime.fromtimestamp(event.timestamp).isoformat()

        # Save snapshot with annotation
        if snapshot is not None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{event.event_type.value}_{ts}.jpg"
            path = self.snapshot_dir / filename
            # Annotate snapshot with bounding box
            annotated = annotate_snapshot(snapshot, event)
            cv2.imwrite(str(path), annotated)
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
        import io

        colors = {
            "person_detected": 0x3498db,    # Blue
            "person_dwelling": 0xe74c3c,    # Red
            "vehicle_detected": 0xf39c12,   # Orange
            "vehicle_stopped": 0xf39c12,    # Orange
            "vehicle_parked": 0x2ecc71,     # Green
            "vehicle_left": 0x9b59b6,       # Purple
            "package_detected": 0x9b59b6,   # Purple
        }

        # Build description with more detail
        description = event.description if event.description else event.class_name

        embed = {
            "title": event.event_type.value.replace("_", " ").title(),
            "color": colors.get(event.event_type.value, 0x95a5a6),
            "timestamp": datetime.fromtimestamp(event.timestamp).isoformat(),
            "fields": [{"name": "Detection", "value": f"{description} ({event.confidence:.0%})", "inline": True}]
        }

        if snapshot is not None:
            # Annotate snapshot with bounding box
            annotated = annotate_snapshot(snapshot, event)
            _, buf = cv2.imencode('.jpg', annotated)
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
