"""Settings API routes."""
import logging
from fastapi import APIRouter, HTTPException, Depends

from ..models import (
    DetectionSettings, PTZSettings, PTZConnectionSettings,
    PoseSettings, ClassifierSettings, DisplaySettings, StreamSettings,
    NotificationSettings, DiscordSettings, MQTTSettings
)
from config import load_user_settings, save_user_settings
from auth.dependencies import get_current_user, require_admin, CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


@router.get("")
async def get_settings(user: CurrentUser = Depends(get_current_user)):
    """Get current settings (authenticated users)."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not loaded")

    # Check if models are loaded
    pose = _state.get("pose")
    classifier = _state.get("classifier")

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
            "loaded": pose is not None and pose.is_loaded if hasattr(pose, 'is_loaded') else pose is not None,
        },
        "classifier": {
            "enabled": cfg.enable_classifier,
            "loaded": classifier is not None and classifier.is_loaded if classifier else False,
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
async def update_detection(settings: DetectionSettings, admin: CurrentUser = Depends(require_admin)):
    """Update detection settings (admin only)."""
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
async def update_ptz(settings: PTZSettings, admin: CurrentUser = Depends(require_admin)):
    """Update PTZ tracking settings (admin only)."""
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
async def update_ptz_connection(settings: PTZConnectionSettings, admin: CurrentUser = Depends(require_admin)):
    """Update PTZ connection settings (admin only, requires restart to reconnect)."""
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
async def update_pose(settings: PoseSettings, admin: CurrentUser = Depends(require_admin)):
    """Update pose settings (admin only) - loads model dynamically if needed."""
    cfg = _state["config"]
    pose = _state.get("pose")

    if cfg:
        cfg.enable_pose = settings.enabled

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["pose"] = {"enabled": settings.enabled}
    save_user_settings(user_settings)

    # Check if model is loaded
    model_loaded = pose is not None and (pose.is_loaded if hasattr(pose, 'is_loaded') else True)

    # If enabling and model not loaded, try to load it
    if settings.enabled and not model_loaded:
        try:
            from pose import PoseEstimator
            new_pose = PoseEstimator(cfg.pose_model_path, cfg.confidence)
            if new_pose.load():
                _state["pose"] = new_pose
                logger.info("Pose estimator loaded dynamically")
                return {"status": "ok", "enabled": True, "loaded": True}
            else:
                logger.warning("Failed to load pose model")
                return {"status": "ok", "enabled": True, "loaded": False, "note": "Model failed to load"}
        except Exception as e:
            logger.error(f"Failed to load pose estimator: {e}")
            return {"status": "ok", "enabled": True, "loaded": False, "note": str(e)}

    logger.info(f"Updated pose: enabled={settings.enabled}, model_loaded={model_loaded}")
    return {"status": "ok", "enabled": settings.enabled, "loaded": model_loaded}


@router.post("/classifier")
async def update_classifier(settings: ClassifierSettings, admin: CurrentUser = Depends(require_admin)):
    """Update classifier settings (admin only) - loads model dynamically if needed."""
    cfg = _state["config"]
    classifier = _state.get("classifier")

    if cfg:
        cfg.enable_classifier = settings.enabled

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["classifier"] = {"enabled": settings.enabled}
    save_user_settings(user_settings)

    # If enabling and model not loaded, try to load it
    model_loaded = classifier is not None and classifier.is_loaded if classifier else False

    if settings.enabled and not model_loaded:
        try:
            from classifier import ImageClassifier
            new_classifier = ImageClassifier(cfg.classifier_model_path, cfg.confidence)
            if new_classifier.load():
                _state["classifier"] = new_classifier
                logger.info("Classifier loaded dynamically")
                return {"status": "ok", "enabled": True, "loaded": True}
            else:
                logger.warning("Failed to load classifier model")
                return {"status": "ok", "enabled": True, "loaded": False, "note": "Model failed to load"}
        except Exception as e:
            logger.error(f"Failed to load classifier: {e}")
            return {"status": "ok", "enabled": True, "loaded": False, "note": str(e)}

    logger.info(f"Updated classifier: enabled={settings.enabled}, model_loaded={model_loaded}")
    return {"status": "ok", "enabled": settings.enabled, "loaded": model_loaded}


@router.post("/display")
async def update_display(settings: DisplaySettings, admin: CurrentUser = Depends(require_admin)):
    """Update display/detection toggle settings (admin only)."""
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
async def update_stream(settings: StreamSettings, admin: CurrentUser = Depends(require_admin)):
    """Update stream/resolution settings (admin only) and restart capture."""
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


@router.get("/notifications")
async def get_notifications(user: CurrentUser = Depends(get_current_user)):
    """Get current notification settings (authenticated)."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not loaded")

    return {
        "discord": {
            "enabled": cfg.enable_discord,
            "webhook_url": cfg.discord_webhook or "",
        },
        "mqtt": {
            "enabled": cfg.enable_mqtt,
            "broker": cfg.mqtt_broker,
            "port": cfg.mqtt_port,
            "topic": cfg.mqtt_topic,
        },
        "save_snapshots": cfg.save_snapshots,
    }


@router.post("/notifications")
async def update_notifications(settings: NotificationSettings, admin: CurrentUser = Depends(require_admin)):
    """Update notification settings (admin only)."""
    cfg = _state["config"]
    notifier = _state.get("notifier")

    if cfg:
        # Update Discord settings
        cfg.enable_discord = settings.discord.enabled
        if settings.discord.webhook_url:
            cfg.discord_webhook = settings.discord.webhook_url

        # Update MQTT settings
        cfg.enable_mqtt = settings.mqtt.enabled
        cfg.mqtt_broker = settings.mqtt.broker
        cfg.mqtt_port = settings.mqtt.port
        cfg.mqtt_topic = settings.mqtt.topic

        # Update snapshot setting
        cfg.save_snapshots = settings.save_snapshots

    # Save to settings.json
    user_settings = load_user_settings()
    user_settings["notifications"] = {
        "discord": {
            "enabled": settings.discord.enabled,
            "webhook_url": settings.discord.webhook_url,
        },
        "mqtt": {
            "enabled": settings.mqtt.enabled,
            "broker": settings.mqtt.broker,
            "port": settings.mqtt.port,
            "topic": settings.mqtt.topic,
        },
        "save_snapshots": settings.save_snapshots,
    }
    save_user_settings(user_settings)

    # Dynamically add/remove handlers
    if notifier:
        if settings.discord.enabled and settings.discord.webhook_url:
            notifier.add_discord(settings.discord.webhook_url)
        else:
            notifier.remove_discord()

        if settings.mqtt.enabled and settings.mqtt.broker:
            notifier.add_mqtt(settings.mqtt.broker, settings.mqtt.port, settings.mqtt.topic)
        else:
            notifier.remove_mqtt()

    logger.info(f"Updated notifications: discord={settings.discord.enabled}, mqtt={settings.mqtt.enabled}")
    return {"status": "ok"}


@router.post("/notifications/discord")
async def update_discord(settings: DiscordSettings, admin: CurrentUser = Depends(require_admin)):
    """Update Discord notification settings (admin only)."""
    cfg = _state["config"]
    notifier = _state.get("notifier")

    if cfg:
        cfg.enable_discord = settings.enabled
        if settings.webhook_url:
            cfg.discord_webhook = settings.webhook_url

    # Save to settings.json
    user_settings = load_user_settings()
    if "notifications" not in user_settings:
        user_settings["notifications"] = {}
    user_settings["notifications"]["discord"] = {
        "enabled": settings.enabled,
        "webhook_url": settings.webhook_url,
    }
    save_user_settings(user_settings)

    # Dynamically add/remove Discord handler
    if notifier:
        if settings.enabled and settings.webhook_url:
            notifier.add_discord(settings.webhook_url)
            logger.info(f"Discord handler added: {settings.webhook_url[:50]}...")
        else:
            notifier.remove_discord()
            logger.info("Discord handler removed")

    logger.info(f"Updated Discord: enabled={settings.enabled}")
    return {"status": "ok"}


@router.post("/notifications/mqtt")
async def update_mqtt(settings: MQTTSettings, admin: CurrentUser = Depends(require_admin)):
    """Update MQTT notification settings (admin only)."""
    cfg = _state["config"]
    notifier = _state.get("notifier")

    if cfg:
        cfg.enable_mqtt = settings.enabled
        cfg.mqtt_broker = settings.broker
        cfg.mqtt_port = settings.port
        cfg.mqtt_topic = settings.topic

    # Save to settings.json
    user_settings = load_user_settings()
    if "notifications" not in user_settings:
        user_settings["notifications"] = {}
    user_settings["notifications"]["mqtt"] = {
        "enabled": settings.enabled,
        "broker": settings.broker,
        "port": settings.port,
        "topic": settings.topic,
    }
    save_user_settings(user_settings)

    # Dynamically add/remove MQTT handler
    if notifier:
        if settings.enabled and settings.broker:
            notifier.add_mqtt(settings.broker, settings.port, settings.topic)
            logger.info(f"MQTT handler added: {settings.broker}:{settings.port}")
        else:
            notifier.remove_mqtt()
            logger.info("MQTT handler removed")

    logger.info(f"Updated MQTT: enabled={settings.enabled}, broker={settings.broker}")
    return {"status": "ok"}
