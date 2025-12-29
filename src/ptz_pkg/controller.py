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
        self._imaging_service = None
        self._video_source_token = None
        self._profile_token = None
        self._connected = False
        self._last_person_time = time.time()  # Initialize to now, not 0
        self._last_command_time = 0
        self._command_interval = 0.15  # Only send commands every 150ms
        self._is_home = True
        self._is_moving = False
        self._move_start_time = 0  # Track when continuous movement started
        self._max_move_duration = 3.0  # Max seconds to move without re-detection
        self._last_direction = (0, 0)  # Track last pan/tilt direction
        self._consecutive_detections = 0  # Count consecutive frames with person
        self._detection_threshold = 3  # Frames required before tracking starts
        self._ir_light_on = False
        self._night_mode_on = False
        # Camera movement tracking for parking system
        self._last_movement_time = 0  # When camera last moved (for parking system)
        self._camera_settle_time = 5.0  # Seconds to wait after camera stops before trusting positions

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

            # Try to get imaging service for light/IR control
            try:
                self._imaging_service = self._camera.create_imaging_service()
                logger.info("Imaging service available")
            except Exception as e:
                logger.debug(f"Imaging service not available: {e}")
                self._imaging_service = None

            # Get profile token
            profiles = self._media_service.GetProfiles()
            if profiles:
                self._profile_token = profiles[0].token
                logger.info(f"PTZ connected, profile: {self._profile_token}")
                self._connected = True

                # Get video source token for imaging service
                try:
                    video_sources = self._media_service.GetVideoSources()
                    if video_sources:
                        self._video_source_token = video_sources[0].token
                        logger.info(f"Video source token: {self._video_source_token}")
                except Exception as e:
                    logger.debug(f"Could not get video source: {e}")

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

        now = time.time()

        # Safety check: stop if moving too long in same direction
        if self._is_moving and now - self._move_start_time > self._max_move_duration:
            logger.warning("PTZ safety stop: max move duration exceeded")
            self.stop()
            return

        # Throttle commands
        if now - self._last_command_time < self._command_interval:
            return
        self._last_command_time = now

        # Track when movement starts or direction changes significantly
        new_direction = (pan, tilt)
        if not self._is_moving or self._direction_changed(new_direction):
            self._move_start_time = now
            self._last_direction = new_direction

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
            self._last_movement_time = time.time()  # Track for parking system
        except Exception as e:
            logger.error(f"PTZ move error: {e}")

    def _direction_changed(self, new_direction: tuple) -> bool:
        """Check if movement direction changed significantly."""
        old_pan, old_tilt = self._last_direction
        new_pan, new_tilt = new_direction
        # Direction changed if sign flipped or magnitude changed significantly
        pan_changed = (old_pan * new_pan < 0) or abs(old_pan - new_pan) > 0.5
        tilt_changed = (old_tilt * new_tilt < 0) or abs(old_tilt - new_tilt) > 0.5
        return pan_changed or tilt_changed

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
            self._last_direction = (0, 0)
            self._move_start_time = 0
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
            self._last_movement_time = time.time()  # Track for parking system
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
            self._last_movement_time = time.time()  # Track for parking system
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

    @property
    def last_movement_time(self) -> float:
        """Get timestamp of last camera movement."""
        return self._last_movement_time

    def camera_recently_moved(self, within_seconds: float = None) -> bool:
        """
        Check if camera moved recently.
        Used by parking system to avoid false "vehicle left" events.
        """
        if within_seconds is None:
            within_seconds = self._camera_settle_time
        return time.time() - self._last_movement_time < within_seconds

    def camera_is_settled(self) -> bool:
        """
        Check if camera has been stable long enough to trust object positions.
        """
        return not self._is_moving and not self.camera_recently_moved()

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
            # No person detected - IMMEDIATELY stop any movement
            if self._is_moving:
                self.stop()

            # Reset consecutive detection counter
            self._consecutive_detections = 0

            # Check if we should return home after delay
            time_since_person = time.time() - self._last_person_time

            if self.config.return_home and time_since_person > self.config.home_delay:
                # Only go home once, not every frame
                if not self._is_home:
                    self.go_home()
                    self._is_home = True
            return

        # Person detected - increment consecutive detection counter
        self._consecutive_detections += 1

        # Don't track until we've seen a person for enough frames (filter false positives)
        if self._consecutive_detections < self._detection_threshold:
            logger.debug(f"Person detected, confirming... ({self._consecutive_detections}/{self._detection_threshold})")
            return

        # Confirmed person - reset home state and update time
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

    def set_ir_light(self, enabled: bool) -> bool:
        """
        Toggle IR illuminator/light on camera.
        Uses ONVIF imaging service or auxiliary commands.
        """
        if not self._connected:
            return False

        self._ir_light_on = enabled
        logger.info(f"IR light {'on' if enabled else 'off'}")

        # Try imaging service first (IrCutFilter controls IR LEDs on many cameras)
        if self._imaging_service and self._video_source_token:
            try:
                # Get current imaging settings
                settings = self._imaging_service.GetImagingSettings({
                    'VideoSourceToken': self._video_source_token
                })

                # Try to set IR cut filter mode
                # OFF = IR LEDs on (night mode), ON = IR LEDs off (day mode)
                # AUTO = camera decides
                request = self._imaging_service.create_type('SetImagingSettings')
                request.VideoSourceToken = self._video_source_token
                request.ImagingSettings = settings
                if hasattr(request.ImagingSettings, 'IrCutFilter'):
                    request.ImagingSettings.IrCutFilter = 'OFF' if enabled else 'ON'
                    self._imaging_service.SetImagingSettings(request)
                    logger.info(f"Set IR cut filter to {'OFF' if enabled else 'ON'}")
                    return True
            except Exception as e:
                logger.debug(f"Could not set IR via imaging service: {e}")

        # Try auxiliary command (some cameras use this)
        try:
            # Common auxiliary commands for IR lights
            aux_data = 'tt:IR' if enabled else 'tt:IRoff'
            request = self._ptz_service.create_type('SendAuxiliaryCommand')
            request.ProfileToken = self._profile_token
            request.AuxiliaryData = aux_data
            self._ptz_service.SendAuxiliaryCommand(request)
            logger.info(f"Sent auxiliary command: {aux_data}")
            return True
        except Exception as e:
            logger.debug(f"Auxiliary command failed: {e}")

        # Try alternative auxiliary commands
        for aux_cmd in ['IRLamp', 'InfraredLamp', 'LED']:
            try:
                request = self._ptz_service.create_type('SendAuxiliaryCommand')
                request.ProfileToken = self._profile_token
                request.AuxiliaryData = f"tt:{aux_cmd}{'On' if enabled else 'Off'}"
                self._ptz_service.SendAuxiliaryCommand(request)
                logger.info(f"IR light command sent via {aux_cmd}")
                return True
            except Exception:
                continue

        logger.warning("IR light control not supported by this camera")
        return False

    def set_night_mode(self, enabled: bool) -> bool:
        """
        Toggle night/day mode (IR cut filter).
        Night mode = IR cut filter OFF (allows IR light through)
        Day mode = IR cut filter ON (blocks IR, true colors)
        """
        if not self._connected:
            return False

        self._night_mode_on = enabled
        logger.info(f"Night mode {'on' if enabled else 'off'}")

        if self._imaging_service and self._video_source_token:
            try:
                # Get current settings
                settings = self._imaging_service.GetImagingSettings({
                    'VideoSourceToken': self._video_source_token
                })

                request = self._imaging_service.create_type('SetImagingSettings')
                request.VideoSourceToken = self._video_source_token
                request.ImagingSettings = settings

                # Set IR cut filter mode
                # OFF = night mode (IR passes through), ON = day mode, AUTO = automatic
                if hasattr(settings, 'IrCutFilter') or True:  # Try anyway
                    request.ImagingSettings.IrCutFilter = 'OFF' if enabled else 'AUTO'
                    self._imaging_service.SetImagingSettings(request)
                    logger.info(f"Night mode set via IrCutFilter")
                    return True

            except Exception as e:
                logger.debug(f"Could not set night mode via imaging: {e}")

        # Try auxiliary command
        try:
            aux_data = 'tt:NightMode' if enabled else 'tt:DayMode'
            request = self._ptz_service.create_type('SendAuxiliaryCommand')
            request.ProfileToken = self._profile_token
            request.AuxiliaryData = aux_data
            self._ptz_service.SendAuxiliaryCommand(request)
            return True
        except Exception as e:
            logger.debug(f"Night mode auxiliary command failed: {e}")

        logger.warning("Night mode control not supported by this camera")
        return False

    def get_imaging_status(self) -> dict:
        """Get current imaging settings status."""
        status = {
            'ir_light': self._ir_light_on,
            'night_mode': self._night_mode_on,
            'imaging_available': self._imaging_service is not None
        }

        if self._imaging_service and self._video_source_token:
            try:
                settings = self._imaging_service.GetImagingSettings({
                    'VideoSourceToken': self._video_source_token
                })
                if hasattr(settings, 'IrCutFilter'):
                    status['ir_cut_filter'] = str(settings.IrCutFilter)
                    # Night mode is typically when IrCutFilter is OFF
                    status['night_mode'] = settings.IrCutFilter == 'OFF'
                    self._night_mode_on = status['night_mode']
            except Exception as e:
                logger.debug(f"Could not get imaging status: {e}")

        return status

    def pan_tilt_reset(self) -> bool:
        """
        Perform pan/tilt correction (positional reset/calibration).
        This recalibrates the camera's pan and tilt position.
        """
        if not self._connected:
            return False

        logger.info("Performing pan/tilt correction reset...")

        # Stop any current movement first
        self.stop()

        # Try multiple methods - cameras vary in how they expose this

        # Method 1: Try auxiliary command (most common for PTZ reset)
        aux_commands = [
            'tt:PTReset',           # Common reset command
            'tt:PanTiltReset',      # Alternative naming
            'tt:Calibration',       # Some cameras use this
            'tt:PositionReset',     # Another variant
            'tt:HomeReset',         # Reset to factory home
            'PTReset',              # Without tt: prefix
            'PanTiltCorrection',    # Direct naming
        ]

        for aux_cmd in aux_commands:
            try:
                request = self._ptz_service.create_type('SendAuxiliaryCommand')
                request.ProfileToken = self._profile_token
                request.AuxiliaryData = aux_cmd
                self._ptz_service.SendAuxiliaryCommand(request)
                logger.info(f"Pan/tilt reset sent via auxiliary command: {aux_cmd}")
                return True
            except Exception as e:
                logger.debug(f"Auxiliary command {aux_cmd} failed: {e}")
                continue

        # Method 2: Try SetHomePosition then GotoHome (resets to a known state)
        try:
            # First try to set current position as home, then reset
            request = self._ptz_service.create_type('SetHomePosition')
            request.ProfileToken = self._profile_token
            self._ptz_service.SetHomePosition(request)
            logger.info("Set home position for reset")
        except Exception as e:
            logger.debug(f"SetHomePosition failed: {e}")

        # Method 3: Move to absolute 0,0 position (center)
        try:
            request = self._ptz_service.create_type('AbsoluteMove')
            request.ProfileToken = self._profile_token
            request.Position = {
                'PanTilt': {'x': 0, 'y': 0},
                'Zoom': {'x': 0}
            }
            self._ptz_service.AbsoluteMove(request)
            logger.info("Pan/tilt reset via AbsoluteMove to 0,0")
            self._is_home = True
            return True
        except Exception as e:
            logger.debug(f"AbsoluteMove failed: {e}")

        # Method 4: Try going to home position as fallback
        try:
            self.go_home()
            logger.info("Pan/tilt reset via GotoHomePosition")
            return True
        except Exception as e:
            logger.debug(f"GotoHome failed: {e}")

        logger.warning("Pan/tilt reset not supported by this camera")
        return False
