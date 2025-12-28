"""PTZ camera control via ONVIF for person tracking."""
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PTZConfig:
    """PTZ configuration."""
    enabled: bool = False
    host: str = ""
    port: int = 2020
    username: str = ""
    password: str = ""

    # Tracking behavior
    track_speed: float = 0.5  # 0.0-1.0, how fast to pan/tilt
    deadzone: float = 0.15  # Don't move if target within this % of center
    return_home: bool = True  # Return to home position when no person
    home_delay: float = 10.0  # Seconds before returning home


class PTZController:
    """ONVIF PTZ controller for tracking people."""

    def __init__(self, config: PTZConfig):
        self.config = config
        self._camera = None
        self._ptz_service = None
        self._media_service = None
        self._profile_token = None
        self._connected = False
        self._last_person_time = 0
        self._is_home = True

    def connect(self) -> bool:
        """Connect to camera via ONVIF."""
        if not self.config.enabled:
            return False

        try:
            from onvif import ONVIFCamera
            import onvif

            # Find WSDL directory
            onvif_path = os.path.dirname(onvif.__file__)
            wsdl_dir = os.path.join(onvif_path, 'wsdl')

            logger.info(f"Connecting to PTZ camera at {self.config.host}:{self.config.port}")

            self._camera = ONVIFCamera(
                self.config.host,
                self.config.port,
                self.config.username,
                self.config.password,
                wsdl_dir=wsdl_dir
            )

            # Get services
            self._media_service = self._camera.create_media_service()
            self._ptz_service = self._camera.create_ptz_service()

            # Get profile token
            profiles = self._media_service.GetProfiles()
            if profiles:
                self._profile_token = profiles[0].token
                logger.info(f"PTZ connected, profile: {self._profile_token}")
                self._connected = True
                return True
            else:
                logger.error("No media profiles found")
                return False

        except Exception as e:
            logger.error(f"PTZ connection failed: {e}")
            return False

    def move(self, pan: float, tilt: float):
        """
        Move camera continuously.
        pan: -1.0 (left) to 1.0 (right)
        tilt: -1.0 (down) to 1.0 (up)
        """
        if not self._connected:
            return

        try:
            request = self._ptz_service.create_type('ContinuousMove')
            request.ProfileToken = self._profile_token
            request.Velocity = {
                'PanTilt': {'x': pan * self.config.track_speed, 'y': tilt * self.config.track_speed},
                'Zoom': {'x': 0}
            }
            self._ptz_service.ContinuousMove(request)
            self._is_home = False
        except Exception as e:
            logger.error(f"PTZ move error: {e}")

    def stop(self):
        """Stop all PTZ movement."""
        if not self._connected:
            return

        try:
            request = self._ptz_service.create_type('Stop')
            request.ProfileToken = self._profile_token
            request.PanTilt = True
            request.Zoom = True
            self._ptz_service.Stop(request)
        except Exception as e:
            logger.error(f"PTZ stop error: {e}")

    def go_home(self):
        """Return to home/preset position."""
        if not self._connected or self._is_home:
            return

        try:
            request = self._ptz_service.create_type('GotoHomePosition')
            request.ProfileToken = self._profile_token
            self._ptz_service.GotoHomePosition(request)
            self._is_home = True
            logger.info("PTZ returning to home position")
        except Exception as e:
            logger.error(f"PTZ home error: {e}")

    def track_person(self, detections: list, frame_width: int, frame_height: int):
        """
        Track the largest person in frame.
        Only tracks 'person' class, ignores vehicles.
        """
        if not self._connected:
            return

        # Filter for people only
        people = [d for d in detections if d.class_name == "person"]

        if not people:
            # No person detected - maybe return home
            if self.config.return_home:
                if time.time() - self._last_person_time > self.config.home_delay:
                    self.stop()
                    self.go_home()
            return

        self._last_person_time = time.time()

        # Find largest person (closest to camera)
        largest = max(people, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))

        # Calculate center of person
        x1, y1, x2, y2 = largest.bbox
        person_cx = (x1 + x2) / 2
        person_cy = (y1 + y2) / 2

        # Normalize to -1.0 to 1.0 (0 = center)
        offset_x = (person_cx - frame_width / 2) / (frame_width / 2)
        offset_y = (person_cy - frame_height / 2) / (frame_height / 2)

        # Check deadzone
        if abs(offset_x) < self.config.deadzone and abs(offset_y) < self.config.deadzone:
            self.stop()
            return

        # Move camera to center person
        # Invert Y because camera tilt up is positive but screen Y increases downward
        pan = offset_x if abs(offset_x) > self.config.deadzone else 0
        tilt = -offset_y if abs(offset_y) > self.config.deadzone else 0

        self.move(pan, tilt)

    def disconnect(self):
        """Disconnect from camera."""
        self.stop()
        self._connected = False
        logger.info("PTZ disconnected")
