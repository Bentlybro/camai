"""Notification system - Discord, MQTT, file logging."""
import json
import logging
import time
import threading
from pathlib import Path
from datetime import datetime
from queue import Queue, Empty
from typing import Optional, Tuple
import numpy as np
import cv2

from tracking.events import Event

logger = logging.getLogger(__name__)

def extract_head_crop_from_keypoints(frame: np.ndarray, keypoints: list, padding: float = 0.5) -> Optional[np.ndarray]:
    """
    Extract head region using pose estimation keypoints.

    COCO keypoints 0-4 are head points: nose, left_eye, right_eye, left_ear, right_ear

    Args:
        frame: Full frame
        keypoints: List of [x, y, confidence] for 17 COCO keypoints
        padding: Extra padding around head region

    Returns:
        Cropped head image or None
    """
    if frame is None or not keypoints:
        return None

    try:
        h, w = frame.shape[:2]

        # Head keypoints: nose(0), left_eye(1), right_eye(2), left_ear(3), right_ear(4)
        head_points = []
        min_conf = 0.3  # Minimum confidence to use a keypoint

        for i in range(5):  # First 5 keypoints are head
            if i < len(keypoints):
                x, y, conf = keypoints[i]
                if conf > min_conf and x > 0 and y > 0:
                    head_points.append((x, y))

        if len(head_points) < 2:
            # Not enough head keypoints detected
            return None

        # Calculate bounding box around head points
        xs = [p[0] for p in head_points]
        ys = [p[1] for p in head_points]

        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        # Estimate head size from keypoint spread
        spread_x = max(xs) - min(xs) if len(xs) > 1 else 50
        spread_y = max(ys) - min(ys) if len(ys) > 1 else 50

        # Head is roughly square, use larger dimension
        head_size = max(spread_x, spread_y, 40)  # Minimum 40px

        # Add padding to get full head
        head_size = head_size * (1 + padding)

        # Calculate crop box centered on head
        x1 = int(cx - head_size)
        y1 = int(cy - head_size * 0.6)  # Head extends more above center
        x2 = int(cx + head_size)
        y2 = int(cy + head_size * 0.8)  # Less below (neck area)

        # Clamp to frame bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return None

        head_crop = frame[y1:y2, x1:x2].copy()

        # Resize to standard size for display (150x150 minimum)
        if head_crop.shape[0] < 150 or head_crop.shape[1] < 150:
            scale = max(150 / head_crop.shape[0], 150 / head_crop.shape[1])
            new_w = int(head_crop.shape[1] * scale)
            new_h = int(head_crop.shape[0] * scale)
            head_crop = cv2.resize(head_crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        return head_crop

    except Exception as e:
        logger.debug(f"Head extraction from keypoints failed: {e}")
        return None


def extract_head_crop_from_bbox(frame: np.ndarray, bbox: tuple, padding: float = 0.2) -> Optional[np.ndarray]:
    """
    Fallback: Extract head region from upper portion of person bounding box.

    Args:
        frame: Full frame
        bbox: Person bounding box (x1, y1, x2, y2)
        padding: Extra padding

    Returns:
        Cropped head image or None
    """
    if frame is None or not bbox:
        return None

    try:
        x1, y1, x2, y2 = [int(c) for c in bbox]
        h, w = frame.shape[:2]

        # Clamp to frame bounds
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 <= x1 or y2 <= y1:
            return None

        person_h = y2 - y1
        person_w = x2 - x1

        # Head is roughly top 25% of person bbox, centered horizontally
        head_h = int(person_h * 0.28)
        head_w = int(person_w * 0.6)  # Head narrower than shoulders

        # Center the head crop horizontally
        center_x = (x1 + x2) // 2
        hx1 = center_x - head_w // 2
        hx2 = center_x + head_w // 2
        hy1 = y1
        hy2 = y1 + head_h

        # Add padding
        pad_x = int(head_w * padding)
        pad_y = int(head_h * padding)
        hx1 = max(0, hx1 - pad_x)
        hy1 = max(0, hy1 - pad_y)
        hx2 = min(w, hx2 + pad_x)
        hy2 = min(h, hy2 + pad_y)

        if hx2 <= hx1 or hy2 <= hy1:
            return None

        head_crop = frame[hy1:hy2, hx1:hx2].copy()

        # Resize to standard size for display (150x150 minimum)
        if head_crop.shape[0] < 150 or head_crop.shape[1] < 150:
            scale = max(150 / head_crop.shape[0], 150 / head_crop.shape[1])
            new_w = int(head_crop.shape[1] * scale)
            new_h = int(head_crop.shape[0] * scale)
            head_crop = cv2.resize(head_crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

        return head_crop

    except Exception as e:
        logger.debug(f"Head extraction from bbox failed: {e}")
        return None


def extract_head_crop(frame: np.ndarray, bbox: tuple, keypoints: list = None, padding: float = 0.3) -> Optional[np.ndarray]:
    """
    Extract head crop using best available method.

    Priority:
    1. Pose estimation keypoints (most accurate)
    2. Upper portion of person bbox (fallback)

    Args:
        frame: Full frame
        bbox: Person bounding box (x1, y1, x2, y2)
        keypoints: Optional pose keypoints [x, y, conf] for this person
        padding: Extra padding around head

    Returns:
        Cropped head image or None
    """
    if frame is None:
        return None

    # Try pose keypoints first (most accurate for distant/low-res subjects)
    if keypoints:
        head_crop = extract_head_crop_from_keypoints(frame, keypoints, padding)
        if head_crop is not None:
            logger.debug("Head extracted using pose keypoints")
            return head_crop

    # Fallback to bbox-based estimation
    if bbox:
        head_crop = extract_head_crop_from_bbox(frame, bbox, padding)
        if head_crop is not None:
            logger.debug("Head extracted using bbox estimation")
            return head_crop

    return None


def find_matching_keypoints(bbox: tuple, all_keypoints: list) -> Optional[list]:
    """
    Find the keypoints that best match a person bounding box.

    Args:
        bbox: Person bounding box (x1, y1, x2, y2)
        all_keypoints: List of keypoints for all detected people

    Returns:
        Keypoints for the matching person, or None
    """
    if not all_keypoints or not bbox:
        return None

    x1, y1, x2, y2 = [int(c) for c in bbox]
    bbox_cx = (x1 + x2) / 2
    bbox_cy = (y1 + y2) / 2

    best_match = None
    best_dist = float('inf')

    for person_kpts in all_keypoints:
        # Calculate center of visible keypoints
        visible_points = []
        for kpt in person_kpts:
            x, y, conf = kpt
            if conf > 0.3 and x > 0 and y > 0:
                visible_points.append((x, y))

        if not visible_points:
            continue

        # Center of this person's keypoints
        kpt_cx = sum(p[0] for p in visible_points) / len(visible_points)
        kpt_cy = sum(p[1] for p in visible_points) / len(visible_points)

        # Distance from bbox center
        dist = ((kpt_cx - bbox_cx) ** 2 + (kpt_cy - bbox_cy) ** 2) ** 0.5

        # Check if keypoints are within or near the bbox
        if x1 - 50 <= kpt_cx <= x2 + 50 and y1 - 50 <= kpt_cy <= y2 + 50:
            if dist < best_dist:
                best_dist = dist
                best_match = person_kpts

    return best_match


def create_combined_snapshot(full_frame: np.ndarray, head_crop: np.ndarray,
                            event: Event) -> np.ndarray:
    """
    Create a combined image with annotated full frame and head crop inset.

    The head crop is placed in the top-right corner of the annotated frame.
    """
    # Annotate the full frame first
    annotated = annotate_snapshot(full_frame, event)

    if head_crop is None:
        return annotated

    h, w = annotated.shape[:2]
    hh, hw = head_crop.shape[:2]

    # Limit head crop size (max 200px or 25% of frame)
    max_size = min(200, w // 4, h // 4)
    if hh > max_size or hw > max_size:
        scale = max_size / max(hh, hw)
        hw = int(hw * scale)
        hh = int(hh * scale)
        head_crop = cv2.resize(head_crop, (hw, hh))

    # Position in top-right corner with margin
    margin = 10
    x_pos = w - hw - margin
    y_pos = margin

    # Draw border around head crop
    border = 3
    cv2.rectangle(annotated,
                  (x_pos - border, y_pos - border),
                  (x_pos + hw + border, y_pos + hh + border),
                  (0, 255, 0), border)

    # Add "HEAD" label
    cv2.putText(annotated, "HEAD", (x_pos, y_pos - border - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # Overlay head crop
    annotated[y_pos:y_pos+hh, x_pos:x_pos+hw] = head_crop

    return annotated


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

    def notify(self, event: Event, snapshot: np.ndarray = None, keypoints: list = None):
        """Queue notification with optional pose keypoints for head extraction."""
        snap_copy = snapshot.copy() if snapshot is not None else None
        self._queue.put((event, snap_copy, keypoints))

    def _worker(self):
        while self._running:
            try:
                event, snapshot, keypoints = self._queue.get(timeout=1)
                for handler in self._handlers:
                    try:
                        handler.send(event, snapshot, keypoints)
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

    def send(self, event: Event, snapshot: np.ndarray = None, keypoints: list = None):
        entry = event.to_dict()
        entry["timestamp_iso"] = datetime.fromtimestamp(event.timestamp).isoformat()

        # Save snapshot with annotation
        if snapshot is not None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{event.event_type.value}_{ts}.jpg"
            path = self.snapshot_dir / filename

            # For person events, try to extract head crop
            head_crop = None
            if event.class_name == "person":
                # Find matching keypoints for this person
                person_keypoints = find_matching_keypoints(event.bbox, keypoints)
                head_crop = extract_head_crop(snapshot, event.bbox, person_keypoints)
                if head_crop is not None:
                    # Save separate head crop
                    head_filename = f"{event.event_type.value}_{ts}_head.jpg"
                    head_path = self.snapshot_dir / head_filename
                    cv2.imwrite(str(head_path), head_crop)
                    entry["head_snapshot"] = str(head_path)
                    entry["head_snapshot_path"] = f"/api/snapshots/{head_filename}"
                    logger.debug(f"Head crop saved: {head_filename}")

            # Create combined snapshot (full frame with head inset if available)
            combined = create_combined_snapshot(snapshot, head_crop, event)
            cv2.imwrite(str(path), combined)
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

    def send(self, event: Event, snapshot: np.ndarray = None, keypoints: list = None):
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
            # For person events, try to extract head crop
            head_crop = None
            if event.class_name == "person":
                person_keypoints = find_matching_keypoints(event.bbox, keypoints)
                head_crop = extract_head_crop(snapshot, event.bbox, person_keypoints)
                if head_crop is not None:
                    embed["fields"].append({"name": "Head", "value": "Captured", "inline": True})

            # Create combined snapshot (with head inset if available)
            combined = create_combined_snapshot(snapshot, head_crop, event)
            _, buf = cv2.imencode('.jpg', combined)
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

    def send(self, event: Event, snapshot: np.ndarray = None, keypoints: list = None):
        if self.client:
            self.client.publish(f"{self.topic}/{event.event_type.value}", json.dumps(event.to_dict()))
