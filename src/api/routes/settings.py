"""Settings API routes."""
import logging
from fastapi import APIRouter, HTTPException

from ..models import (
    DetectionSettings, PTZSettings, PTZConnectionSettings,
    PoseSettings, DisplaySettings, StreamSettings
)
from config import load_user_settings, save_user_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


@router.get("")
async def get_settings():
    """Get current settings."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not loaded")

    return {
        "detection": {
            "confidence": cfg.confidence,
            "iou_threshold": cfg.iou_threshold,
        },
        "ptz": {
            "enabled": cfg.enable_ptz,
            "track_speed": cfg.ptz_track_speed,
            "deadzone": cfg.ptz_deadzone,
            "host": cfg.ptz_host,
            "port": cfg.ptz_port,
            "username": cfg.ptz_username,
            "has_password": bool(cfg.ptz_password),
        },
        "pose": {
            "enabled": cfg.enable_pose,
        },
        "display": {
            "show_overlays": cfg.show_overlays,
            "detect_person": cfg.detect_person,
            "detect_vehicle": cfg.detect_vehicle,
            "detect_package": cfg.detect_package,
        },
        "stream": {
            "quality": 70,
            "width": cfg.capture_width,
            "height": cfg.capture_height,
        }
    }


@router.post("/detection")
async def update_detection(settings: DetectionSettings):
    """Update detection settings."""
    cfg = _state["config"]
    detector = _state["detector"]

    if cfg:
        cfg.confidence = settings.confidence
        cfg.iou_threshold = settings.iou_threshold

    if detector:
        detector.confidence = settings.confidence
        detector.iou_threshold = settings.iou_threshold

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["detection"] = {
        "confidence": settings.confidence,
        "iou_threshold": settings.iou_threshold,
    }
    save_user_settings(user_settings)

    logger.info(f"Updated detection: conf={settings.confidence}, iou={settings.iou_threshold}")
    return {"status": "ok"}


@router.post("/ptz")
async def update_ptz(settings: PTZSettings):
    """Update PTZ tracking settings."""
    cfg = _state["config"]
    ptz = _state["ptz"]

    # Load existing settings to merge
    user_settings = load_user_settings()
    existing_ptz = user_settings.get("ptz", {})

    track_speed = settings.track_speed if settings.track_speed != 0.5 else existing_ptz.get("track_speed", settings.track_speed)
    deadzone = settings.deadzone if settings.deadzone != 0.15 else existing_ptz.get("deadzone", settings.deadzone)

    if cfg:
        cfg.enable_ptz = settings.enabled
        cfg.ptz_track_speed = track_speed
        cfg.ptz_deadzone = deadzone

    if ptz and ptz.config:
        ptz.config.enabled = settings.enabled  # Update PTZ controller's enabled state
        ptz.config.track_speed = track_speed
        ptz.config.deadzone = deadzone

    # Save to settings.json
    user_settings["ptz"] = {
        **existing_ptz,
        "enabled": settings.enabled,
        "track_speed": track_speed,
        "deadzone": deadzone,
    }
    save_user_settings(user_settings)

    logger.info(f"Updated PTZ: enabled={settings.enabled}, speed={track_speed}")
    return {"status": "ok"}


@router.post("/ptz/connection")
async def update_ptz_connection(settings: PTZConnectionSettings):
    """Update PTZ connection settings (requires restart to reconnect)."""
    cfg = _state["config"]

    if cfg:
        cfg.ptz_host = settings.host
        cfg.ptz_port = settings.port
        cfg.ptz_username = settings.username
        if settings.password:
            cfg.ptz_password = settings.password

    # Save to settings.json
    user_settings = load_user_settings()
    if "ptz" not in user_settings:
        user_settings["ptz"] = {}
    user_settings["ptz"]["host"] = settings.host
    user_settings["ptz"]["port"] = settings.port
    user_settings["ptz"]["username"] = settings.username
    if settings.password:
        user_settings["ptz"]["password"] = settings.password
    save_user_settings(user_settings)

    logger.info(f"Updated PTZ connection: host={settings.host}, port={settings.port}")
    return {"status": "ok", "note": "Restart required to reconnect PTZ"}


@router.post("/pose")
async def update_pose(settings: PoseSettings):
    """Update pose settings."""
    cfg = _state["config"]

    if cfg:
        cfg.enable_pose = settings.enabled

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["pose"] = {"enabled": settings.enabled}
    save_user_settings(user_settings)

    logger.info(f"Updated pose: enabled={settings.enabled}")
    return {"status": "ok", "note": "Restart required for pose model changes"}


@router.post("/display")
async def update_display(settings: DisplaySettings):
    """Update display/detection toggle settings."""
    cfg = _state["config"]

    if cfg:
        cfg.show_overlays = settings.show_overlays
        cfg.detect_person = settings.detect_person
        cfg.detect_vehicle = settings.detect_vehicle
        cfg.detect_package = settings.detect_package

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["display"] = {
        "show_overlays": settings.show_overlays,
    }
    if "detection" not in user_settings:
        user_settings["detection"] = {}
    user_settings["detection"]["detect_person"] = settings.detect_person
    user_settings["detection"]["detect_vehicle"] = settings.detect_vehicle
    user_settings["detection"]["detect_package"] = settings.detect_package
    save_user_settings(user_settings)

    logger.info(f"Updated display: overlays={settings.show_overlays}")
    return {"status": "ok"}


@router.post("/stream")
async def update_stream(settings: StreamSettings):
    """Update stream/resolution settings and restart capture."""
    cfg = _state["config"]
    capture = _state["capture"]

    if cfg:
        cfg.capture_width = settings.width
        cfg.capture_height = settings.height

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["stream"] = {
        "width": settings.width,
        "height": settings.height,
        "quality": settings.quality,
    }
    save_user_settings(user_settings)

    # Restart capture with new resolution
    if capture:
        logger.info(f"Restarting capture with new resolution: {settings.width}x{settings.height}")
        capture.restart(settings.width, settings.height)

    logger.info(f"Updated stream: {settings.width}x{settings.height}")
    return {"status": "ok", "message": f"Resolution changed to {settings.width}x{settings.height}"}
