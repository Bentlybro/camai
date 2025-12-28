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
        self._last_command_time = 0
        self._command_interval = 0.15  # Only send commands every 150ms
        self._is_home = True
        self._is_moving = False

    def connect(self) -> bool:
        """Connect to camera via ONVIF."""
        if not self.config.host:
            logger.warning("PTZ host not configured")
            return False

        try:
            from onvif import ONVIFCamera
            import onvif

            # Find WSDL directory - check multiple locations
            wsdl_dir = None
            possible_paths = [
                os.path.join(os.path.dirname(onvif.__file__), 'wsdl'),
                '/tmp/onvif-wsdl/wsdl',
                '/usr/share/onvif/wsdl',
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    wsdl_dir = path
                    break

            if not wsdl_dir:
                logger.error("WSDL directory not found")
                return False

            logger.info(f"Connecting to PTZ camera at {self.config.host}:{self.config.port}")
            logger.info(f"Using WSDL: {wsdl_dir}")
            logger.info(f"Username: {self.config.username}")

            self._camera = ONVIFCamera(
                self.config.host,
                self.config.port,
                self.config.username,
                self.config.password,
                wsdl_dir=wsdl_dir,
                no_cache=True  # Disable cache for fresh connection
            )

            # Get device info first to verify connection
            try:
                device_info = self._camera.devicemgmt.GetDeviceInformation()
                logger.info(f"Camera: {device_info.Manufacturer} {device_info.Model}")
            except Exception as e:
                logger.warning(f"Could not get device info: {e}")

            # Get services
            self._media_service = self._camera.create_media_service()
            self._ptz_service = self._camera.create_ptz_service()

            # Get profile token
            profiles = self._media_service.GetProfiles()
            if profiles:
                self._profile_token = profiles[0].token
                logger.info(f"PTZ connected, profile: {self._profile_token}")
                self._connected = True

                # Try to get PTZ status/capabilities
                try:
                    ptz_configs = self._ptz_service.GetConfigurations()
                    if ptz_configs:
                        logger.info(f"PTZ configurations: {len(ptz_configs)}")
                except Exception as e:
                    logger.debug(f"Could not get PTZ configs: {e}")

                return True
            else:
                logger.error("No media profiles found")
                return False

        except Exception as e:
            logger.error(f"PTZ connection failed: {e}")
            # Show more detail for common errors
            error_str = str(e).lower()
            if "401" in error_str or "unauthorized" in error_str:
                logger.error("Authentication failed - check username/password")
            elif "timeout" in error_str or "timed out" in error_str:
                logger.error("Connection timeout - check IP address and port")
            elif "refused" in error_str:
                logger.error("Connection refused - is ONVIF enabled on camera?")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def move(self, pan: float, tilt: float):
        """
        Move camera continuously.
        pan: -1.0 (left) to 1.0 (right)
        tilt: -1.0 (down) to 1.0 (up)
        """
        if not self._connected:
            return

        # Throttle commands
        now = time.time()
        if now - self._last_command_time < self._command_interval:
            return
        self._last_command_time = now

        try:
            request = self._ptz_service.create_type('ContinuousMove')
            request.ProfileToken = self._profile_token
            request.Velocity = {
                'PanTilt': {'x': pan * self.config.track_speed, 'y': tilt * self.config.track_speed},
                'Zoom': {'x': 0}
            }
            self._ptz_service.ContinuousMove(request)
            self._is_home = False
            self._is_moving = True
        except Exception as e:
            logger.error(f"PTZ move error: {e}")

    def stop(self):
        """Stop all PTZ movement."""
        if not self._connected or not self._is_moving:
            return

        try:
            request = self._ptz_service.create_type('Stop')
            request.ProfileToken = self._profile_token
            request.PanTilt = True
            request.Zoom = True
            self._ptz_service.Stop(request)
            self._is_moving = False
        except Exception as e:
            logger.error(f"PTZ stop error: {e}")

    def go_home(self):
        """Return to home position (preset 1 or GotoHomePosition)."""
        if not self._connected:
            return False

        try:
            # Try GotoHomePosition first
            request = self._ptz_service.create_type('GotoHomePosition')
            request.ProfileToken = self._profile_token
            self._ptz_service.GotoHomePosition(request)
            self._is_home = True
            logger.info("PTZ going to home position")
            return True
        except Exception as e:
            logger.debug(f"GotoHomePosition not supported: {e}")
            # Fallback to preset 1
            return self.goto_preset("1")

    def get_presets(self) -> list:
        """Get list of saved presets."""
        if not self._connected:
            return []

        try:
            presets = self._ptz_service.GetPresets({'ProfileToken': self._profile_token})
            result = []
            for p in presets:
                result.append({
                    'token': p.token,
                    'name': getattr(p, 'Name', p.token)
                })
            return result
        except Exception as e:
            logger.error(f"Failed to get presets: {e}")
            return []

    def goto_preset(self, preset_token: str) -> bool:
        """Go to a saved preset position."""
        if not self._connected:
            return False

        try:
            request = self._ptz_service.create_type('GotoPreset')
            request.ProfileToken = self._profile_token
            request.PresetToken = preset_token
            self._ptz_service.GotoPreset(request)
            self._is_home = (preset_token == "1")
            logger.info(f"PTZ going to preset: {preset_token}")
            return True
        except Exception as e:
            logger.error(f"Failed to goto preset: {e}")
            return False

    def set_preset(self, preset_name: str = None) -> str:
        """Save current position as a preset. Returns preset token."""
        if not self._connected:
            return None

        try:
            request = self._ptz_service.create_type('SetPreset')
            request.ProfileToken = self._profile_token
            if preset_name:
                request.PresetName = preset_name
            response = self._ptz_service.SetPreset(request)
            token = response.PresetToken if hasattr(response, 'PresetToken') else str(response)
            logger.info(f"PTZ preset saved: {preset_name or token}")
            return token
        except Exception as e:
            logger.error(f"Failed to set preset: {e}")
            return None

    def remove_preset(self, preset_token: str) -> bool:
        """Remove a saved preset."""
        if not self._connected:
            return False

        try:
            request = self._ptz_service.create_type('RemovePreset')
            request.ProfileToken = self._profile_token
            request.PresetToken = preset_token
            self._ptz_service.RemovePreset(request)
            logger.info(f"PTZ preset removed: {preset_token}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove preset: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        """Check if PTZ is connected."""
        return self._connected

    def track_person(self, detections: list, frame_width: int, frame_height: int):
        """
        Track the largest person in frame.
        Only tracks 'person' class, ignores vehicles.
        Returns to home (preset 1) after home_delay seconds with no person.
        """
        if not self._connected:
            return

        # Filter for people only
        people = [d for d in detections if d.class_name == "person"]

        if not people:
            # No person detected
            time_since_person = time.time() - self._last_person_time

            if self.config.return_home and time_since_person > self.config.home_delay:
                # Only go home once, not every frame
                if not self._is_home:
                    self.stop()
                    self.go_home()
                    self._is_home = True
            return

        # Person detected - reset home state and update time
        self._last_person_time = time.time()
        self._is_home = False

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
