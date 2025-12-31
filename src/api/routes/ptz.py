"""PTZ control API routes."""
import logging
import threading
import time
from fastapi import APIRouter, HTTPException, Depends

from ..models import PTZMoveRequest, AutoTrackRequest
from auth.dependencies import get_current_user, require_admin, CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ptz", tags=["ptz"])

# Reference to global state (set by app.py)
_state = None

# PTZ auto-reset timer for non-admin users (5 minutes)
PTZ_AUTO_RESET_SECONDS = 5 * 60
_ptz_reset_timer: threading.Timer = None
_ptz_last_activity: float = 0


def set_state(state: dict):
    global _state
    _state = state


def _reset_ptz_to_home():
    """Reset PTZ to home position and re-enable auto-tracking."""
    global _ptz_reset_timer
    _ptz_reset_timer = None

    ptz = _state.get("ptz") if _state else None
    cfg = _state.get("config") if _state else None

    if ptz and ptz._connected:
        logger.info("PTZ auto-reset: Going home and re-enabling auto-tracking")
        ptz.go_home()
        if cfg:
            cfg.enable_ptz = True


def _schedule_ptz_reset(user: CurrentUser):
    """Schedule PTZ reset for non-admin users. Admins are exempt."""
    global _ptz_reset_timer, _ptz_last_activity

    # Admin users don't trigger auto-reset
    if user.is_admin:
        return

    _ptz_last_activity = time.time()

    # Cancel existing timer
    if _ptz_reset_timer:
        _ptz_reset_timer.cancel()

    # Schedule new reset
    _ptz_reset_timer = threading.Timer(PTZ_AUTO_RESET_SECONDS, _reset_ptz_to_home)
    _ptz_reset_timer.daemon = True
    _ptz_reset_timer.start()
    logger.debug(f"PTZ auto-reset scheduled in {PTZ_AUTO_RESET_SECONDS}s for user {user.username}")


@router.post("/move")
async def ptz_move(request: PTZMoveRequest, user: CurrentUser = Depends(get_current_user)):
    """Move PTZ camera manually (authenticated)."""
    ptz = _state.get("ptz")
    if not ptz:
        logger.warning("PTZ move failed: PTZ not initialized")
        raise HTTPException(status_code=503, detail="PTZ not initialized")
    if not ptz._connected:
        logger.warning("PTZ move failed: PTZ not connected")
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Temporarily disable auto-tracking while manually controlling
    cfg = _state.get("config")
    if cfg:
        cfg.enable_ptz = False

    # Schedule auto-reset for non-admin users
    _schedule_ptz_reset(user)

    logger.info(f"PTZ move command: pan={request.pan}, tilt={request.tilt}")
    success = ptz.move(request.pan, request.tilt)
    return {"status": "ok" if success else "throttled", "pan": request.pan, "tilt": request.tilt, "sent": success}


@router.post("/stop")
async def ptz_stop(user: CurrentUser = Depends(get_current_user)):
    """Stop PTZ movement (authenticated)."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Schedule auto-reset for non-admin users
    _schedule_ptz_reset(user)

    logger.debug("PTZ stop command received")
    ptz._is_moving = True  # Force stop to send command
    ptz.stop()
    return {"status": "ok"}


@router.get("/status")
async def ptz_status(user: CurrentUser = Depends(get_current_user)):
    """Get PTZ connection status (authenticated)."""
    ptz = _state["ptz"]
    cfg = _state["config"]

    return {
        "connected": ptz is not None and ptz._connected if ptz else False,
        "auto_tracking": cfg.enable_ptz if cfg else False,
    }


@router.post("/auto-track")
async def set_auto_track(request: AutoTrackRequest, user: CurrentUser = Depends(get_current_user)):
    """Enable or disable auto-tracking (authenticated)."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not available")

    cfg.enable_ptz = request.enabled

    # If non-admin is disabling auto-track, schedule reset
    if not request.enabled:
        _schedule_ptz_reset(user)

    return {
        "status": "ok",
        "auto_tracking": cfg.enable_ptz,
    }


@router.post("/home")
async def ptz_home(user: CurrentUser = Depends(get_current_user)):
    """Go to home position (authenticated)."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Schedule auto-reset for non-admin users
    _schedule_ptz_reset(user)

    logger.info("PTZ home command received")
    success = ptz.go_home()
    return {"status": "ok" if success else "failed", "sent": success}


@router.get("/presets")
async def get_presets(user: CurrentUser = Depends(get_current_user)):
    """Get list of saved presets (authenticated)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        return []

    return ptz.get_presets()


@router.post("/presets")
async def save_preset(name: str = None, admin: CurrentUser = Depends(require_admin)):
    """Save current position as a preset (admin only)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    token = ptz.set_preset(name)
    if token:
        return {"status": "ok", "token": token, "name": name}
    raise HTTPException(status_code=500, detail="Failed to save preset")


@router.post("/presets/{token}/goto")
async def goto_preset(token: str, user: CurrentUser = Depends(get_current_user)):
    """Go to a saved preset (authenticated)."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Disable auto-tracking when using presets
    cfg = _state.get("config")
    if cfg:
        cfg.enable_ptz = False

    # Schedule auto-reset for non-admin users
    _schedule_ptz_reset(user)

    logger.info(f"PTZ goto preset: {token}")
    success = ptz.goto_preset(token)
    return {"status": "ok" if success else "failed", "sent": success}


@router.delete("/presets/{token}")
async def delete_preset(token: str, admin: CurrentUser = Depends(require_admin)):
    """Delete a saved preset (admin only)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.remove_preset(token)
    return {"status": "ok" if success else "failed"}


@router.get("/imaging")
async def get_imaging_status(user: CurrentUser = Depends(get_current_user)):
    """Get imaging status (light, night mode) (authenticated)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        return {
            "ir_light": False,
            "night_mode": False,
            "imaging_available": False
        }

    return ptz.get_imaging_status()


@router.post("/light")
async def toggle_ir_light(enabled: bool = True, admin: CurrentUser = Depends(require_admin)):
    """Toggle IR light on/off (admin only)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.set_ir_light(enabled)
    return {
        "status": "ok" if success else "unsupported",
        "ir_light": enabled if success else ptz._ir_light_on
    }


@router.post("/night-mode")
async def toggle_night_mode(enabled: bool = True, admin: CurrentUser = Depends(require_admin)):
    """Toggle night mode (IR cut filter) on/off (admin only)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.set_night_mode(enabled)
    return {
        "status": "ok" if success else "unsupported",
        "night_mode": enabled if success else ptz._night_mode_on
    }


@router.post("/reset")
async def pan_tilt_reset(admin: CurrentUser = Depends(require_admin)):
    """Perform pan/tilt correction (positional reset/calibration) (admin only)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Disable auto-tracking during reset
    cfg = _state["config"]
    if cfg:
        cfg.enable_ptz = False

    success = ptz.pan_tilt_reset()
    return {
        "status": "ok" if success else "unsupported",
        "message": "Pan/tilt correction initiated" if success else "Reset command not supported by camera"
    }
