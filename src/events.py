"""Event detection - person dwell, package detection, vehicle stops."""
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum
from collections import defaultdict

from detector import Detection

logger = logging.getLogger(__name__)


class EventType(Enum):
    PERSON_DETECTED = "person_detected"
    PERSON_DWELLING = "person_dwelling"
    PERSON_LEFT = "person_left"
    VEHICLE_DETECTED = "vehicle_detected"
    VEHICLE_STOPPED = "vehicle_stopped"
    PACKAGE_DETECTED = "package_detected"
    PACKAGE_REMOVED = "package_removed"


@dataclass
class Event:
    """Detected event."""
    event_type: EventType
    timestamp: float
    class_name: str
    confidence: float
    bbox: tuple
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.event_type.value,
            "timestamp": self.timestamp,
            "class": self.class_name,
            "confidence": float(self.confidence),
            "bbox": [int(x) for x in self.bbox],
            **{k: float(v) if hasattr(v, 'item') else v for k, v in self.metadata.items()}
        }


@dataclass
class TrackedObject:
    """Object tracked across frames."""
    id: int
    class_name: str
    first_seen: float
    last_seen: float
    bbox: tuple
    confidence: float
    reported: bool = False


class EventDetector:
    """Detects events from object detections over time."""

    def __init__(
        self,
        person_dwell_time: float = 3.0,
        person_cooldown: float = 30.0,
        vehicle_stop_time: float = 5.0,
        iou_threshold: float = 0.3,
    ):
        self.person_dwell = person_dwell_time
        self.person_cooldown = person_cooldown
        self.vehicle_stop = vehicle_stop_time
        self.iou_threshold = iou_threshold

        self._objects: Dict[int, TrackedObject] = {}
        self._next_id = 0
        self._last_person_event = 0
        self._callbacks: List[Callable] = []

    def on_event(self, callback: Callable[[Event], None]):
        """Register event callback."""
        self._callbacks.append(callback)

    def _fire(self, event: Event):
        """Fire event to callbacks."""
        logger.info(f"Event: {event.event_type.value} - {event.class_name}")
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _iou(self, a: tuple, b: tuple) -> float:
        """Calculate IoU between two boxes."""
        x1 = max(a[0], b[0])
        y1 = max(a[1], b[1])
        x2 = min(a[2], b[2])
        y2 = min(a[3], b[3])
        inter = max(0, x2-x1) * max(0, y2-y1)
        area_a = (a[2]-a[0]) * (a[3]-a[1])
        area_b = (b[2]-b[0]) * (b[3]-b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0

    def _match(self, det: Detection) -> Optional[int]:
        """Match detection to tracked object."""
        best_iou, best_id = 0, None
        for oid, obj in self._objects.items():
            if obj.class_name != det.class_name:
                continue
            iou = self._iou(det.bbox, obj.bbox)
            if iou > best_iou and iou >= self.iou_threshold:
                best_iou, best_id = iou, oid
        return best_id

    def update(self, detections: List[Detection], w: int = 0, h: int = 0) -> List[Event]:
        """Update with new detections, return events."""
        now = time.time()
        events = []
        matched = set()

        for det in detections:
            oid = self._match(det)

            if oid is not None:
                # Update existing
                matched.add(oid)
                obj = self._objects[oid]
                obj.last_seen = now
                obj.bbox = det.bbox
                obj.confidence = det.confidence

                dwell = now - obj.first_seen

                # Person dwelling
                if obj.class_name == "person" and dwell >= self.person_dwell and not obj.reported:
                    if now - self._last_person_event >= self.person_cooldown:
                        obj.reported = True
                        self._last_person_event = now
                        event = Event(EventType.PERSON_DWELLING, now, "person", det.confidence, det.bbox, {"dwell": dwell})
                        events.append(event)
                        self._fire(event)

                # Vehicle stopped
                elif obj.class_name in ("car", "truck") and dwell >= self.vehicle_stop and not obj.reported:
                    obj.reported = True
                    event = Event(EventType.VEHICLE_STOPPED, now, obj.class_name, det.confidence, det.bbox, {"stop_time": dwell})
                    events.append(event)
                    self._fire(event)

            else:
                # New object
                oid = self._next_id
                self._next_id += 1
                self._objects[oid] = TrackedObject(oid, det.class_name, now, now, det.bbox, det.confidence)

                if det.class_name == "person":
                    event = Event(EventType.PERSON_DETECTED, now, "person", det.confidence, det.bbox)
                    events.append(event)
                    self._fire(event)
                elif det.class_name in ("car", "truck"):
                    event = Event(EventType.VEHICLE_DETECTED, now, det.class_name, det.confidence, det.bbox)
                    events.append(event)
                    self._fire(event)
                elif det.class_name == "package":
                    event = Event(EventType.PACKAGE_DETECTED, now, "package", det.confidence, det.bbox)
                    events.append(event)
                    self._fire(event)

        # Remove stale objects (not seen for 2 seconds)
        stale = [oid for oid, obj in self._objects.items() if now - obj.last_seen > 2.0]
        for oid in stale:
            del self._objects[oid]

        return events

    @property
    def tracked_count(self) -> dict:
        counts = defaultdict(int)
        for obj in self._objects.values():
            counts[obj.class_name] += 1
        return dict(counts)
