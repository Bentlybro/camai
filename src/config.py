"""Configuration management - loads from settings.json with .env as fallback."""
import os
import json
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from project root
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass

# Settings file path
SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"


def load_user_settings() -> dict:
    """Load user settings from settings.json."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_settings(settings: dict):
    """Save user settings to settings.json."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Failed to save settings: {e}")


@dataclass
class Config:
    """All configuration in one place."""
    # Camera
    rtsp_url: str = ""
    capture_width: int = 640
    capture_height: int = 480
    target_fps: int = 30

    # Detection
    model_path: str = "yolo11n.engine"
    confidence: float = 0.5
    iou_threshold: float = 0.45

    # Pose estimation
    enable_pose: bool = False
    pose_model_path: str = "yolo11n-pose.engine"

    # Classes: 0=person, 2=car, 7=truck, 24=backpack, 26=handbag, 28=suitcase
    target_classes: List[int] = field(default_factory=lambda: [0, 2, 7, 24, 26, 28])
    class_names: dict = field(default_factory=lambda: {
        0: "person", 2: "car", 7: "truck",
        24: "package", 26: "package", 28: "package"
    })

    # Detection toggles (which classes to detect)
    detect_person: bool = True
    detect_vehicle: bool = True
    detect_package: bool = True

    # Display options
    show_overlays: bool = True

    # Events
    person_dwell_time: float = 3.0
    person_cooldown: float = 30.0
    vehicle_stop_time: float = 5.0

    # Notifications
    enable_mqtt: bool = False
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic: str = "camai/events"

    enable_discord: bool = False
    discord_webhook: Optional[str] = None

    save_snapshots: bool = True
    snapshot_dir: str = "snapshots"
    log_dir: str = "logs"

    # Stream output
    enable_stream: bool = True
    stream_port: int = 8080

    # PTZ tracking
    enable_ptz: bool = False
    ptz_host: str = ""
    ptz_port: int = 2020
    ptz_username: str = ""
    ptz_password: str = ""
    ptz_track_speed: float = 0.5
    ptz_deadzone: float = 0.15
    ptz_return_home: bool = True
    ptz_home_delay: float = 10.0

    # Debug
    debug: bool = False

    def __post_init__(self):
        """Load values from settings.json first, then .env as fallback."""
        user = load_user_settings()

        # Camera - .env for connection info, settings.json for resolution
        self.rtsp_url = os.getenv("RTSP_URL", "rtsp://user:pass@192.168.0.36:554/stream1")
        self.capture_width = user.get("stream", {}).get("width") or int(os.getenv("CAPTURE_WIDTH", "640"))
        self.capture_height = user.get("stream", {}).get("height") or int(os.getenv("CAPTURE_HEIGHT", "480"))
        self.target_fps = int(os.getenv("TARGET_FPS", "30"))

        # Detection - prefer settings.json
        self.model_path = os.getenv("MODEL_PATH", "yolo11n.engine")
        self.confidence = user.get("detection", {}).get("confidence") or float(os.getenv("CONFIDENCE", "0.5"))
        self.iou_threshold = user.get("detection", {}).get("iou_threshold") or float(os.getenv("IOU_THRESHOLD", "0.45"))

        # Pose - prefer settings.json
        self.enable_pose = user.get("pose", {}).get("enabled", os.getenv("ENABLE_POSE", "false").lower() == "true")
        self.pose_model_path = os.getenv("POSE_MODEL_PATH", "yolo11n-pose.engine")

        # Detection toggles - prefer settings.json
        detection_cfg = user.get("detection", {})
        self.detect_person = detection_cfg.get("detect_person", True)
        self.detect_vehicle = detection_cfg.get("detect_vehicle", True)
        self.detect_package = detection_cfg.get("detect_package", True)

        # Display options - prefer settings.json
        display_cfg = user.get("display", {})
        self.show_overlays = display_cfg.get("show_overlays", True)

        # Events - from .env
        self.person_dwell_time = float(os.getenv("PERSON_DWELL_TIME", "3.0"))
        self.person_cooldown = float(os.getenv("PERSON_COOLDOWN", "30.0"))
        self.vehicle_stop_time = float(os.getenv("VEHICLE_STOP_TIME", "5.0"))

        # Notifications - from .env
        self.enable_mqtt = os.getenv("ENABLE_MQTT", "false").lower() == "true"
        self.mqtt_broker = os.getenv("MQTT_BROKER", "localhost")
        self.mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
        self.mqtt_topic = os.getenv("MQTT_TOPIC", "camai/events")
        self.enable_discord = os.getenv("ENABLE_DISCORD", "false").lower() == "true"
        self.discord_webhook = os.getenv("DISCORD_WEBHOOK")
        self.save_snapshots = os.getenv("SAVE_SNAPSHOTS", "true").lower() == "true"
        self.snapshot_dir = os.getenv("SNAPSHOT_DIR", "snapshots")
        self.log_dir = os.getenv("LOG_DIR", "logs")

        # Stream - from .env
        self.enable_stream = os.getenv("ENABLE_STREAM", "true").lower() == "true"
        self.stream_port = int(os.getenv("STREAM_PORT", "8080"))

        # PTZ - prefer settings.json for tunable params
        self.enable_ptz = user.get("ptz", {}).get("enabled", os.getenv("ENABLE_PTZ", "false").lower() == "true")
        self.ptz_host = os.getenv("PTZ_HOST", "")
        self.ptz_port = int(os.getenv("PTZ_PORT", "2020"))
        self.ptz_username = os.getenv("PTZ_USERNAME", "")
        self.ptz_password = os.getenv("PTZ_PASSWORD", "")
        self.ptz_track_speed = user.get("ptz", {}).get("track_speed") or float(os.getenv("PTZ_TRACK_SPEED", "0.5"))
        self.ptz_deadzone = user.get("ptz", {}).get("deadzone") or float(os.getenv("PTZ_DEADZONE", "0.15"))
        self.ptz_return_home = os.getenv("PTZ_RETURN_HOME", "true").lower() == "true"
        self.ptz_home_delay = float(os.getenv("PTZ_HOME_DELAY", "10.0"))

        # Debug
        self.debug = os.getenv("DEBUG", "false").lower() == "true"


# Singleton config
_config = None


def get_config() -> Config:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config
