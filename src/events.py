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
    VEHICLE_PARKED = "vehicle_parked"
    VEHICLE_LEFT = "vehicle_left"
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
    color: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        result = {
            "type": self.event_type.value,
            "timestamp": self.timestamp,
            "class": self.class_name,
            "confidence": float(self.confidence),
            "bbox": [int(x) for x in self.bbox],
            **{k: float(v) if hasattr(v, 'item') else v for k, v in self.metadata.items()}
        }
        if self.color:
            result["color"] = self.color
        if self.description:
            result["description"] = self.description
        return result


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
    # Classification info
    color: str = ""
    description: str = ""
    signature: str = ""  # e.g., "black_truck" for better matching


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
        self._last_events: Dict[str, float] = {}  # Cooldowns per event type
        self._event_cooldown = 30.0  # Seconds between same event type
        self._callbacks: List[Callable] = []

        # === PARKING DETECTION SYSTEM ===
        # Parked vehicles: cars that have been stationary for 3+ minutes
        self._parked_vehicles: Dict[int, dict] = {}  # pid -> {bbox, first_seen, last_seen, signature, notified}
        self._parking_time = 180.0  # 3 minutes to be considered "parked"
        self._parked_gone_timeout = 30.0  # 30 seconds of not seeing = car left
        self._parked_iou_threshold = 0.3  # Threshold to match parked position

        # Stopped vehicles: cars that stopped recently but not yet "parked"
        self._stopped_vehicles: Dict[int, dict] = {}  # sid -> {bbox, first_seen, last_seen, signature, notified}
        self._stopped_gone_timeout = 10.0  # 10 seconds of not seeing = car moved on

        # Track recent detection positions to prevent spam from same location
        self._recent_detections: Dict[str, List[dict]] = {"person": [], "vehicle": [], "package": []}
        self._detection_location_cooldown = 30.0  # Seconds before same spot can trigger again
        self._location_iou_threshold = 0.5  # How much overlap = "same spot"

        # Time-based cooldown for vehicle_detected (cars driving by)
        self._vehicle_detected_cooldown = 10.0  # Short cooldown for moving vehicles
        self._last_vehicle_detected = 0

        # Global rate limiting
        self._notification_times: List[float] = []
        self._max_notifications_per_minute = 10

        # Startup flag - register existing vehicles as parked after a delay
        self._startup_time = time.time()
        self._startup_scan_done = False
        self._startup_scan_delay = 10.0  # Wait 10 seconds then scan for existing parked cars

    def on_event(self, callback: Callable[[Event], None]):
        """Register event callback."""
        self._callbacks.append(callback)

    def _can_fire(self, event_type: str) -> bool:
        """Check if we can fire this event type (cooldown + rate limit check)."""
        now = time.time()

        # Check per-event-type cooldown
        last = self._last_events.get(event_type, 0)
        if now - last < self._event_cooldown:
            return False

        # Check global rate limit
        self._notification_times = [t for t in self._notification_times if now - t < 60]
        if len(self._notification_times) >= self._max_notifications_per_minute:
            logger.debug(f"Rate limited: {len(self._notification_times)} notifications in last minute")
            return False

        self._last_events[event_type] = now
        self._notification_times.append(now)
        return True

    def _fire(self, event: Event):
        """Fire event to callbacks."""
        # Show description (e.g., "black truck") if available, otherwise just class
        display_name = event.description if event.description else event.class_name
        logger.info(f"Event: {event.event_type.value} - {display_name}")
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
        """Match detection to tracked object using class, position, and signature."""
        best_score, best_id = 0, None
        for oid, obj in self._objects.items():
            if obj.class_name != det.class_name:
                continue

            iou = self._iou(det.bbox, obj.bbox)
            if iou < self.iou_threshold:
                continue

            # Base score is IoU
            score = iou

            # Boost score if signatures match (same color vehicle/person)
            if det.signature and obj.signature and det.signature == obj.signature:
                score += 0.3  # Significant boost for matching signature

            if score > best_score:
                best_score, best_id = score, oid

        return best_id

    def _get_position_id(self, bbox: tuple) -> int:
        """Get a grid-based position ID for a bounding box."""
        cx = (bbox[0] + bbox[2]) // 2
        cy = (bbox[1] + bbox[3]) // 2
        return hash((cx // 40, cy // 40))  # 40-pixel grid

    def _is_known_stationary_vehicle(self, det: Detection) -> bool:
        """Check if detection matches a known parked or stopped vehicle."""
        if det.class_name not in ("car", "truck"):
            return False

        now = time.time()

        # Check parked vehicles first
        for pid, parked in list(self._parked_vehicles.items()):
            iou = self._iou(det.bbox, parked["bbox"])
            if iou >= self._parked_iou_threshold:
                parked["last_seen"] = now
                parked["bbox"] = det.bbox  # Update position (slight drift ok)
                if det.signature and not parked.get("signature"):
                    parked["signature"] = det.signature
                return True

        # Check stopped vehicles
        for sid, stopped in list(self._stopped_vehicles.items()):
            iou = self._iou(det.bbox, stopped["bbox"])
            if iou >= self._parked_iou_threshold:
                stopped["last_seen"] = now
                stopped["bbox"] = det.bbox
                if det.signature and not stopped.get("signature"):
                    stopped["signature"] = det.signature
                return True

        return False

    def _register_stopped_vehicle(self, det: Detection):
        """Register a new stopped vehicle (will become parked after 3 min)."""
        if det.class_name not in ("car", "truck"):
            return

        sid = self._get_position_id(det.bbox)

        if sid not in self._stopped_vehicles:
            now = time.time()
            self._stopped_vehicles[sid] = {
                "bbox": det.bbox,
                "first_seen": now,
                "last_seen": now,
                "class": det.class_name,
                "signature": det.signature,
                "color": det.color,
                "notified_stopped": False,
            }
            logger.debug(f"Registered stopped vehicle at position {sid}")

    def _promote_to_parked(self, sid: int, stopped: dict) -> dict:
        """Promote a stopped vehicle to parked status."""
        pid = self._get_position_id(stopped["bbox"])
        parked = {
            "bbox": stopped["bbox"],
            "first_seen": stopped["first_seen"],
            "last_seen": stopped["last_seen"],
            "class": stopped["class"],
            "signature": stopped.get("signature"),
            "color": stopped.get("color"),
        }
        self._parked_vehicles[pid] = parked
        del self._stopped_vehicles[sid]
        logger.info(f"Vehicle promoted to PARKED at position {pid}")
        return parked

    def _update_parking_system(self, vehicle_detections: List[Detection]) -> List[Event]:
        """Update the parking detection system and return any parking-related events."""
        events = []
        now = time.time()

        # Startup scan: register existing vehicles as parked after delay
        if not self._startup_scan_done and now - self._startup_time > self._startup_scan_delay:
            self._startup_scan_done = True
            for det in vehicle_detections:
                if det.class_name in ("car", "truck"):
                    pid = self._get_position_id(det.bbox)
                    if pid not in self._parked_vehicles:
                        self._parked_vehicles[pid] = {
                            "bbox": det.bbox,
                            "first_seen": now,
                            "last_seen": now,
                            "class": det.class_name,
                            "signature": det.signature,
                            "color": det.color,
                        }
                        logger.info(f"Startup: registered existing parked vehicle at {pid}")

        # Check for stopped vehicles that should become parked (3+ min stationary)
        for sid, stopped in list(self._stopped_vehicles.items()):
            stationary_time = now - stopped["first_seen"]
            if stationary_time >= self._parking_time:
                parked = self._promote_to_parked(sid, stopped)
                # Fire vehicle_parked event
                if self._can_fire("vehicle_parked"):
                    event = Event(
                        EventType.VEHICLE_PARKED, now, parked["class"], 0.9,
                        parked["bbox"], {"parked_duration": stationary_time},
                        color=parked.get("color", ""), description=f"{parked.get('color', '')} {parked['class']} parked".strip()
                    )
                    events.append(event)
                    self._fire(event)

        # Check for stopped vehicles that left (not seen for 10 seconds)
        for sid, stopped in list(self._stopped_vehicles.items()):
            if now - stopped["last_seen"] > self._stopped_gone_timeout:
                logger.debug(f"Stopped vehicle at {sid} moved on")
                del self._stopped_vehicles[sid]

        # Check for parked vehicles that left (not seen for 30 seconds)
        for pid, parked in list(self._parked_vehicles.items()):
            if now - parked["last_seen"] > self._parked_gone_timeout:
                # Fire vehicle_left event
                if self._can_fire("vehicle_left"):
                    parked_duration = now - parked["first_seen"]
                    event = Event(
                        EventType.VEHICLE_LEFT, now, parked["class"], 0.9,
                        parked["bbox"], {"parked_duration": parked_duration},
                        color=parked.get("color", ""), description=f"{parked.get('color', '')} {parked['class']} left".strip()
                    )
                    events.append(event)
                    self._fire(event)
                logger.info(f"Parked vehicle at {pid} has LEFT")
                del self._parked_vehicles[pid]

        return events

    def _is_new_detection_location(self, det: Detection) -> bool:
        """Check if this detection is at a new location (not recently seen)."""
        now = time.time()
        category = "person" if det.class_name == "person" else "vehicle" if det.class_name in ("car", "truck") else "package"

        # Clean up old entries
        self._recent_detections[category] = [
            d for d in self._recent_detections[category]
            if now - d["time"] < self._detection_location_cooldown
        ]

        # Check if this location was recently triggered
        for recent in self._recent_detections[category]:
            iou = self._iou(det.bbox, recent["bbox"])
            if iou >= self._location_iou_threshold:
                # Same location, still in cooldown
                return False

        # New location - record it
        self._recent_detections[category].append({
            "bbox": det.bbox,
            "time": now
        })
        return True

    def update(self, detections: List[Detection], w: int = 0, h: int = 0) -> List[Event]:
        """Update with new detections, return events."""
        now = time.time()
        events = []
        matched = set()

        # Separate vehicle detections for parking system
        vehicle_detections = [d for d in detections if d.class_name in ("car", "truck")]

        # Update parking system (handles parked/left events)
        parking_events = self._update_parking_system(vehicle_detections)
        events.extend(parking_events)

        for det in detections:
            oid = self._match(det)

            if oid is not None:
                # Update existing
                matched.add(oid)
                obj = self._objects[oid]
                obj.last_seen = now
                obj.bbox = det.bbox
                obj.confidence = det.confidence
                # Update classification if available
                if det.signature:
                    obj.signature = det.signature
                    obj.color = det.color
                    obj.description = det.description

                dwell = now - obj.first_seen

                # Person dwelling
                if obj.class_name == "person" and dwell >= self.person_dwell and not obj.reported:
                    if self._can_fire("person_dwelling"):
                        obj.reported = True
                        event = Event(EventType.PERSON_DWELLING, now, "person", det.confidence, det.bbox,
                                      {"dwell": dwell}, color=obj.color, description=obj.description)
                        events.append(event)
                        self._fire(event)

                # Vehicle stopped - register for parking tracking
                elif obj.class_name in ("car", "truck") and dwell >= self.vehicle_stop and not obj.reported:
                    obj.reported = True
                    # Register as stopped (will become parked after 3 min)
                    self._register_stopped_vehicle(det)
                    # Fire vehicle_stopped event
                    if self._can_fire("vehicle_stopped"):
                        event = Event(EventType.VEHICLE_STOPPED, now, obj.class_name, det.confidence, det.bbox,
                                      {"stop_time": dwell}, color=obj.color, description=obj.description)
                        events.append(event)
                        self._fire(event)

            else:
                # New object - check if it's a known stationary vehicle (parked or stopped)
                if self._is_known_stationary_vehicle(det):
                    # This is a parked/stopped car we're tracking - skip
                    continue

                # Create new tracked object
                oid = self._next_id
                self._next_id += 1
                self._objects[oid] = TrackedObject(
                    id=oid,
                    class_name=det.class_name,
                    first_seen=now,
                    last_seen=now,
                    bbox=det.bbox,
                    confidence=det.confidence,
                    color=det.color,
                    description=det.description,
                    signature=det.signature
                )

                # Fire events for new detections
                if det.class_name == "person" and self._is_new_detection_location(det):
                    event = Event(EventType.PERSON_DETECTED, now, "person", det.confidence, det.bbox,
                                  color=det.color, description=det.description)
                    events.append(event)
                    self._fire(event)
                elif det.class_name in ("car", "truck"):
                    # Only fire vehicle_detected for genuinely new vehicles (not parked/stopped)
                    if now - self._last_vehicle_detected >= self._vehicle_detected_cooldown:
                        self._last_vehicle_detected = now
                        event = Event(EventType.VEHICLE_DETECTED, now, det.class_name, det.confidence, det.bbox,
                                      color=det.color, description=det.description)
                        events.append(event)
                        self._fire(event)
                elif det.class_name == "package" and self._is_new_detection_location(det):
                    event = Event(EventType.PACKAGE_DETECTED, now, "package", det.confidence, det.bbox,
                                  color=det.color, description=det.description)
                    events.append(event)
                    self._fire(event)

        # Remove stale objects - longer timeout for vehicles (5s) vs people (2s)
        stale = []
        for oid, obj in self._objects.items():
            timeout = 5.0 if obj.class_name in ("car", "truck") else 2.0
            if now - obj.last_seen > timeout:
                stale.append(oid)
        for oid in stale:
            del self._objects[oid]

        return events

    @property
    def tracked_count(self) -> int:
        """Return total number of tracked objects."""
        return len(self._objects)

    @property
    def tracked_by_class(self) -> dict:
        """Return counts by class."""
        counts = defaultdict(int)
        for obj in self._objects.values():
            counts[obj.class_name] += 1
        return dict(counts)

    @property
    def parking_stats(self) -> dict:
        """Return parking system statistics."""
        return {
            "parked_count": len(self._parked_vehicles),
            "stopped_count": len(self._stopped_vehicles),
            "parked_positions": list(self._parked_vehicles.keys()),
        }
