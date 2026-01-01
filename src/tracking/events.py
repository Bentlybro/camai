"""Event detection - person dwell, package detection, vehicle stops."""
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable
from enum import Enum
from collections import defaultdict

from core.detector import Detection
from tracking.geometry import iou, bbox_center, distance

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
    # Movement tracking for loitering detection
    position_history: list = None  # List of (timestamp, center_x, center_y)
    loitering_start: float = 0  # When they started staying in one area

    def __post_init__(self):
        if self.position_history is None:
            self.position_history = []


class EventDetector:
    """Detects events from object detections over time."""

    def __init__(
        self,
        person_dwell_time: float = 3.0,
        person_cooldown: float = 30.0,
        vehicle_stop_time: float = 5.0,
        iou_threshold: float = 0.3,
    ):
        self.person_dwell = person_dwell_time  # Legacy - now used as loitering time
        self.person_cooldown = person_cooldown
        self.vehicle_stop = vehicle_stop_time
        self.iou_threshold = iou_threshold

        # Loitering detection - person must stay within area for X seconds
        self._loitering_time = 10.0  # Seconds of staying in same area to trigger
        self._loitering_radius = 100  # Pixels - max movement to still be "loitering"
        self._position_history_max = 30  # Keep last 30 position samples

        self._objects: Dict[int, TrackedObject] = {}
        self._next_id = 0
        self._last_events: Dict[str, float] = {}  # Cooldowns per event type
        self._event_cooldown = 30.0  # Seconds between same event type
        self._callbacks: List[Callable] = []

        # === PARKING DETECTION SYSTEM ===
        # Parked vehicles: cars that have been stationary for 3+ minutes
        self._parked_vehicles: Dict[int, dict] = {}  # pid -> {bbox, first_seen, last_seen, signature, notified}
        self._parking_time = 180.0  # 3 minutes to be considered "parked"
        self._parked_gone_timeout = 60.0  # 60 seconds of not seeing = car left (handles YOLO flickering)
        self._parked_iou_threshold = 0.25  # Threshold to match parked position (lowered for box drift)

        # Stopped vehicles: cars that stopped recently but not yet "parked"
        self._stopped_vehicles: Dict[int, dict] = {}  # sid -> {bbox, first_seen, last_seen, signature, notified}
        self._stopped_gone_timeout = 20.0  # 20 seconds of not seeing = car moved on

        # Track recent detection positions to prevent spam from same location
        self._recent_detections: Dict[str, List[dict]] = {"person": [], "vehicle": [], "package": []}
        self._detection_location_cooldown = 30.0  # Seconds before same spot can trigger again
        self._location_iou_threshold = 0.5  # How much overlap = "same spot"

        # Time-based cooldowns for moving objects
        self._vehicle_detected_cooldown = 10.0  # Seconds between vehicle_detected
        self._last_vehicle_detected = 0
        self._person_detected_cooldown = 30.0  # Seconds between person_detected (matches recording alert)
        self._last_person_detected = 0

        # Global rate limiting
        self._notification_times: List[float] = []
        self._max_notifications_per_minute = 3  # Max 3 notifications per minute total

        # Startup flag - register existing vehicles as parked after a delay
        self._startup_time = time.time()
        self._startup_scan_done = False
        self._startup_scan_delay = 10.0  # Wait 10 seconds then scan for existing parked cars

        # Repeated detection tracker - catches flickering detections at night
        # If same position triggers vehicle_detected 2+ times in 2 min, auto-register as parked
        self._detection_history: Dict[int, list] = {}  # position_id -> [timestamps]
        self._repeated_detection_threshold = 2  # 2 detections at same spot = parked
        self._repeated_detection_window = 120.0  # Within 2 minutes

        # PTZ camera reference for movement detection
        self._ptz = None
        self._last_camera_move_handled = 0  # Track when we last handled camera movement
        self._camera_settle_rescan_done = True  # Start True - only set False when camera ACTUALLY moves
        self._camera_move_logged = False  # Only log once per movement session
        self._camera_has_moved = False  # Track if camera has ever moved since startup

    def set_ptz(self, ptz_controller):
        """Set PTZ controller reference for camera movement detection."""
        self._ptz = ptz_controller

    def on_event(self, callback: Callable[[Event], None]):
        """Register event callback."""
        self._callbacks.append(callback)

    def _camera_recently_moved(self) -> bool:
        """Check if PTZ camera recently moved (affects parking detection)."""
        if self._ptz is None:
            return False
        return self._ptz.camera_recently_moved()

    def _handle_camera_movement(self, vehicle_detections: list):
        """
        Handle camera movement - extend timeouts and prepare for rescan.
        Called when camera has recently moved.
        """
        now = time.time()

        self._camera_settle_rescan_done = False  # Need to rescan when camera settles
        self._camera_has_moved = True  # Mark that camera has moved at least once

        # Extend last_seen time for all parked/stopped vehicles to prevent false "left" events
        for pid, parked in self._parked_vehicles.items():
            parked["last_seen"] = now
        for sid, stopped in self._stopped_vehicles.items():
            stopped["last_seen"] = now

        # Only log once per movement session
        if not self._camera_move_logged:
            logger.info("Camera moving - extending parked vehicle timeouts")
            self._camera_move_logged = True

    def _rescan_after_camera_settles(self, vehicle_detections: list):
        """
        After camera settles from movement, re-register visible vehicles as parked.
        This prevents false "vehicle left" events when camera view changes.
        """
        # Only run if camera has actually moved (not at startup)
        if not self._camera_has_moved:
            return

        if self._camera_settle_rescan_done:
            return

        if self._ptz and not self._ptz.camera_is_settled():
            return  # Camera still moving or recently moved

        self._camera_settle_rescan_done = True
        self._camera_move_logged = False  # Reset for next movement
        now = time.time()

        # Clear old parking data since positions are now invalid
        old_parked_count = len(self._parked_vehicles)
        old_stopped_count = len(self._stopped_vehicles)
        self._parked_vehicles.clear()
        self._stopped_vehicles.clear()

        # Re-register all currently visible vehicles as parked
        for det in vehicle_detections:
            if det.class_name in ("car", "truck"):
                pid = self._get_position_id(det.bbox)
                self._parked_vehicles[pid] = {
                    "bbox": det.bbox,
                    "first_seen": now,
                    "last_seen": now,
                    "class": det.class_name,
                    "signature": det.signature,
                    "color": det.color,
                    "description": det.description,
                }

        logger.info(f"Camera settled - re-registered {len(self._parked_vehicles)} vehicles "
                   f"(was: {old_parked_count} parked, {old_stopped_count} stopped)")

    def _update_position_history(self, obj: TrackedObject, bbox: tuple):
        """Update position history for loitering detection."""
        now = time.time()
        cx, cy = bbox_center(bbox)
        obj.position_history.append((now, cx, cy))
        # Keep only recent history
        if len(obj.position_history) > self._position_history_max:
            obj.position_history = obj.position_history[-self._position_history_max:]

    def _is_loitering(self, obj: TrackedObject) -> tuple:
        """
        Check if person is loitering (staying in same area).
        Returns (is_loitering, duration) tuple.
        """
        if len(obj.position_history) < 3:
            return False, 0

        now = time.time()

        # Get positions from the last N seconds
        recent_positions = [
            (t, x, y) for t, x, y in obj.position_history
            if now - t <= self._loitering_time + 2  # Slight buffer
        ]

        if len(recent_positions) < 2:
            return False, 0

        # Calculate movement range
        xs = [p[1] for p in recent_positions]
        ys = [p[2] for p in recent_positions]

        x_range = max(xs) - min(xs)
        y_range = max(ys) - min(ys)
        total_movement = (x_range ** 2 + y_range ** 2) ** 0.5

        # If movement is within radius, they're loitering
        if total_movement <= self._loitering_radius:
            # Calculate how long they've been in this area
            oldest_in_area = recent_positions[0][0]

            # Find when they entered this area
            for i, (t, x, y) in enumerate(obj.position_history):
                # Check distance from current position
                curr_cx, curr_cy = bbox_center(obj.bbox)
                dist = distance((x, y), (curr_cx, curr_cy))
                if dist <= self._loitering_radius:
                    oldest_in_area = t
                    break

            loiter_duration = now - oldest_in_area

            if loiter_duration >= self._loitering_time:
                return True, loiter_duration

        return False, 0

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

    def _match(self, det: Detection) -> Optional[int]:
        """Match detection to tracked object using class, position, and signature."""
        best_score, best_id = 0, None
        for oid, obj in self._objects.items():
            if obj.class_name != det.class_name:
                continue

            overlap = iou(det.bbox, obj.bbox)
            if overlap < self.iou_threshold:
                continue

            # Base score is IoU
            score = overlap

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
            overlap = iou(det.bbox, parked["bbox"])
            # Match by IoU, signature, or color
            sig_match = det.signature and parked.get("signature") and det.signature == parked["signature"]
            color_match = det.color and parked.get("color") and det.color == parked["color"]

            # Very lenient matching - any overlap with matching color, or decent IoU
            if overlap >= 0.15 or sig_match or (color_match and overlap >= 0.05):
                parked["last_seen"] = now
                parked["bbox"] = det.bbox  # Update position
                # Update classification info
                if det.signature and not parked.get("signature"):
                    parked["signature"] = det.signature
                if det.color and not parked.get("color"):
                    parked["color"] = det.color
                if det.description:
                    parked["description"] = det.description
                return True

        # Check stopped vehicles
        for sid, stopped in list(self._stopped_vehicles.items()):
            overlap = iou(det.bbox, stopped["bbox"])
            sig_match = det.signature and stopped.get("signature") and det.signature == stopped["signature"]
            color_match = det.color and stopped.get("color") and det.color == stopped["color"]

            if overlap >= 0.15 or sig_match or (color_match and overlap >= 0.05):
                stopped["last_seen"] = now
                stopped["bbox"] = det.bbox
                if det.signature and not stopped.get("signature"):
                    stopped["signature"] = det.signature
                if det.color and not stopped.get("color"):
                    stopped["color"] = det.color
                if det.description:
                    stopped["description"] = det.description
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
                "description": det.description,
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
            "description": stopped.get("description"),
        }
        self._parked_vehicles[pid] = parked
        del self._stopped_vehicles[sid]
        logger.info(f"Vehicle promoted to PARKED at position {pid}")
        return parked

    def _update_parking_system(self, vehicle_detections: List[Detection]) -> List[Event]:
        """Update the parking detection system and return any parking-related events."""
        events = []
        now = time.time()

        # === PTZ CAMERA MOVEMENT HANDLING ===
        # If camera recently moved, extend timeouts to prevent false "left" events
        if self._camera_recently_moved():
            self._handle_camera_movement(vehicle_detections)
            # Don't process "vehicle left" events while camera is moving
            # Just update last_seen for any matched vehicles and return
            for det in vehicle_detections:
                if det.class_name in ("car", "truck"):
                    # Try to match with existing parked/stopped vehicles by signature if available
                    for pid, parked in self._parked_vehicles.items():
                        if det.signature and parked.get("signature") == det.signature:
                            parked["last_seen"] = now
                            parked["bbox"] = det.bbox
                            break
            return events

        # After camera settles, rescan and re-register vehicles
        self._rescan_after_camera_settles(vehicle_detections)

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
                            "description": det.description,
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
            overlap = iou(det.bbox, recent["bbox"])
            if overlap >= self._location_iou_threshold:
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

                # Classification caching: if detection has new classification, update tracked object
                # If detection has no classification, copy from tracked object (cache hit)
                if det.signature:
                    obj.signature = det.signature
                    obj.color = det.color
                    obj.description = det.description
                elif obj.signature:
                    # Copy cached classification to detection (avoid re-classifying)
                    det.signature = obj.signature
                    det.color = obj.color
                    det.description = obj.description

                # Track position history for people (loitering detection)
                if obj.class_name == "person":
                    self._update_position_history(obj, det.bbox)

                # Person loitering - actually staying in one area, not just walking through
                if obj.class_name == "person" and not obj.reported:
                    is_loitering, loiter_duration = self._is_loitering(obj)
                    if is_loitering:
                        if self._can_fire("person_dwelling"):
                            obj.reported = True
                            event = Event(EventType.PERSON_DWELLING, now, "person", det.confidence, det.bbox,
                                          {"dwell": loiter_duration}, color=obj.color,
                                          description=f"{obj.description} loitering" if obj.description else "person loitering")
                            events.append(event)
                            self._fire(event)

                # Vehicle stopped - register for parking tracking
                dwell = now - obj.first_seen
                if obj.class_name in ("car", "truck") and dwell >= self.vehicle_stop and not obj.reported:
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
                if det.class_name == "person":
                    # Skip person_detected if camera recently moved or is still settling
                    # This prevents spam when PTZ tracks a person: detect -> move -> lose -> settle -> re-detect
                    camera_moving = self._camera_recently_moved()
                    camera_settled = self._ptz.camera_is_settled() if self._ptz else True

                    if camera_moving or not camera_settled:
                        logger.debug("Suppressing person_detected - camera moving or settling")
                        continue

                    # Time-based cooldown - one person_detected per 30 seconds max
                    if now - self._last_person_detected >= self._person_detected_cooldown:
                        self._last_person_detected = now
                        event = Event(EventType.PERSON_DETECTED, now, "person", det.confidence, det.bbox,
                                      color=det.color, description=det.description)
                        events.append(event)
                        self._fire(event)
                elif det.class_name in ("car", "truck"):
                    # Check for repeated detections at same position (flickering at night)
                    pos_id = self._get_position_id(det.bbox)

                    # Track detection history
                    if pos_id not in self._detection_history:
                        self._detection_history[pos_id] = []

                    # Clean old entries and add new
                    self._detection_history[pos_id] = [
                        t for t in self._detection_history[pos_id]
                        if now - t < self._repeated_detection_window
                    ]
                    self._detection_history[pos_id].append(now)

                    # If repeated detection, auto-register as parked (flickering car)
                    if len(self._detection_history[pos_id]) >= self._repeated_detection_threshold:
                        if pos_id not in self._parked_vehicles:
                            self._parked_vehicles[pos_id] = {
                                "bbox": det.bbox,
                                "first_seen": self._detection_history[pos_id][0],
                                "last_seen": now,
                                "class": det.class_name,
                                "signature": det.signature,
                                "color": det.color,
                            }
                            logger.info(f"Auto-registered flickering vehicle as PARKED at {pos_id}")
                            # Clear history since it's now parked
                            self._detection_history[pos_id] = []
                        continue  # Don't fire event - it's now parked

                    # Only fire vehicle_detected for genuinely new vehicles
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

        # Remove stale objects - longer timeouts to handle detection flickering
        # Vehicles: 15s (YOLO often loses parked cars briefly)
        # People: 5s (handles brief gaps from fast movement or night conditions)
        stale = []
        for oid, obj in self._objects.items():
            timeout = 15.0 if obj.class_name in ("car", "truck") else 5.0
            if now - obj.last_seen > timeout:
                stale.append(oid)
        for oid in stale:
            del self._objects[oid]

        return events

    def update_classifications(self, detections: list):
        """Update tracked objects with classification info from detections."""
        for det in detections:
            if not det.signature:
                continue
            # Find matching tracked object and update it
            oid = self._match(det)
            if oid is not None and oid in self._objects:
                obj = self._objects[oid]
                if not obj.signature:  # Only update if not already set
                    obj.signature = det.signature
                    obj.color = det.color
                    obj.description = det.description

    @property
    def tracked_count(self) -> int:
        """Return total number of tracked objects (including parked/stopped vehicles)."""
        return len(self._objects) + len(self._parked_vehicles) + len(self._stopped_vehicles)

    @property
    def active_count(self) -> int:
        """Return number of actively moving objects (not parked)."""
        return len(self._objects)

    @property
    def tracked_by_class(self) -> dict:
        """Return counts by class (including parked vehicles)."""
        counts = defaultdict(int)
        for obj in self._objects.values():
            counts[obj.class_name] += 1
        # Include parked vehicles
        for parked in self._parked_vehicles.values():
            counts[parked["class"]] += 1
        # Include stopped vehicles
        for stopped in self._stopped_vehicles.values():
            counts[stopped["class"]] += 1
        return dict(counts)

    @property
    def parking_stats(self) -> dict:
        """Return parking system statistics."""
        return {
            "parked_count": len(self._parked_vehicles),
            "stopped_count": len(self._stopped_vehicles),
            "parked_positions": list(self._parked_vehicles.keys()),
        }

    def get_current_detections(self) -> list:
        """Return list of all currently visible/tracked objects for display."""
        detections = []

        # Active tracked objects
        for obj in self._objects.values():
            detections.append({
                "id": obj.id,
                "class": obj.class_name,
                "color": obj.color or "",
                "description": obj.description or obj.class_name,
                "confidence": round(obj.confidence, 2),
                "status": "active",
            })

        # Parked vehicles - use stored description if available
        for pid, parked in self._parked_vehicles.items():
            # Build description: prefer stored description, fallback to color+class
            desc = parked.get("description", "")
            if not desc:
                color = parked.get("color", "")
                cls = parked.get("class", "car")
                desc = f"{color} {cls}".strip() if color else cls

            detections.append({
                "id": pid,
                "class": parked.get("class", "car"),
                "color": parked.get("color", ""),
                "description": desc,
                "confidence": 0.9,
                "status": "parked",
            })

        # Stopped vehicles - use stored description if available
        for sid, stopped in self._stopped_vehicles.items():
            desc = stopped.get("description", "")
            if not desc:
                color = stopped.get("color", "")
                cls = stopped.get("class", "car")
                desc = f"{color} {cls}".strip() if color else cls

            detections.append({
                "id": sid,
                "class": stopped.get("class", "car"),
                "color": stopped.get("color", ""),
                "description": desc,
                "confidence": 0.9,
                "status": "stopped",
            })

        return detections
