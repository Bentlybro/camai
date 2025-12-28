"""Configuration management - loads from environment and .env file."""
import os
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


@dataclass
class Config:
    """All configuration in one place."""
    # Camera
    rtsp_url: str = os.getenv("RTSP_URL", "rtsp://user:pass@192.168.0.36:554/stream1")
    capture_width: int = int(os.getenv("CAPTURE_WIDTH", "640"))
    capture_height: int = int(os.getenv("CAPTURE_HEIGHT", "480"))
    target_fps: int = int(os.getenv("TARGET_FPS", "30"))

    # Detection
    model_path: str = os.getenv("MODEL_PATH", "yolo11n.engine")
    confidence: float = float(os.getenv("CONFIDENCE", "0.5"))
    iou_threshold: float = float(os.getenv("IOU_THRESHOLD", "0.45"))

    # Classes: 0=person, 2=car, 7=truck, 24=backpack, 26=handbag, 28=suitcase
    target_classes: List[int] = field(default_factory=lambda: [0, 2, 7, 24, 26, 28])
    class_names: dict = field(default_factory=lambda: {
        0: "person", 2: "car", 7: "truck",
        24: "package", 26: "package", 28: "package"
    })

    # Events
    person_dwell_time: float = float(os.getenv("PERSON_DWELL_TIME", "3.0"))
    person_cooldown: float = float(os.getenv("PERSON_COOLDOWN", "30.0"))
    vehicle_stop_time: float = float(os.getenv("VEHICLE_STOP_TIME", "5.0"))

    # Notifications
    enable_mqtt: bool = os.getenv("ENABLE_MQTT", "false").lower() == "true"
    mqtt_broker: str = os.getenv("MQTT_BROKER", "localhost")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_topic: str = os.getenv("MQTT_TOPIC", "camai/events")

    enable_discord: bool = os.getenv("ENABLE_DISCORD", "false").lower() == "true"
    discord_webhook: Optional[str] = os.getenv("DISCORD_WEBHOOK")

    save_snapshots: bool = os.getenv("SAVE_SNAPSHOTS", "true").lower() == "true"
    snapshot_dir: str = os.getenv("SNAPSHOT_DIR", "snapshots")
    log_dir: str = os.getenv("LOG_DIR", "logs")

    # Stream output
    enable_stream: bool = os.getenv("ENABLE_STREAM", "true").lower() == "true"
    stream_port: int = int(os.getenv("STREAM_PORT", "8080"))

    # PTZ tracking
    enable_ptz: bool = os.getenv("ENABLE_PTZ", "false").lower() == "true"
    ptz_host: str = os.getenv("PTZ_HOST", "")  # Camera IP for ONVIF
    ptz_port: int = int(os.getenv("PTZ_PORT", "2020"))
    ptz_username: str = os.getenv("PTZ_USERNAME", "")
    ptz_password: str = os.getenv("PTZ_PASSWORD", "")
    ptz_track_speed: float = float(os.getenv("PTZ_TRACK_SPEED", "0.5"))
    ptz_deadzone: float = float(os.getenv("PTZ_DEADZONE", "0.15"))
    ptz_return_home: bool = os.getenv("PTZ_RETURN_HOME", "true").lower() == "true"
    ptz_home_delay: float = float(os.getenv("PTZ_HOME_DELAY", "10.0"))

    # Debug
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


# Singleton config
_config = None


def get_config() -> Config:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config
