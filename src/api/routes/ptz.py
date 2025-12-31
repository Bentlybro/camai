"""PTZ control API routes."""
import logging
from fastapi import APIRouter, HTTPException

from ..models import PTZMoveRequest, AutoTrackRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ptz", tags=["ptz"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


@router.post("/move")
async def ptz_move(request: PTZMoveRequest):
    """Move PTZ camera manually."""
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

    logger.info(f"PTZ move command: pan={request.pan}, tilt={request.tilt}")
    success = ptz.move(request.pan, request.tilt)
    return {"status": "ok" if success else "throttled", "pan": request.pan, "tilt": request.tilt, "sent": success}


@router.post("/stop")
async def ptz_stop():
    """Stop PTZ movement."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    logger.debug("PTZ stop command received")
    ptz._is_moving = True  # Force stop to send command
    ptz.stop()
    return {"status": "ok"}


@router.get("/status")
async def ptz_status():
    """Get PTZ connection status."""
    ptz = _state["ptz"]
    cfg = _state["config"]

    return {
        "connected": ptz is not None and ptz._connected if ptz else False,
        "auto_tracking": cfg.enable_ptz if cfg else False,
    }


@router.post("/auto-track")
async def set_auto_track(request: AutoTrackRequest):
    """Enable or disable auto-tracking."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not available")

    cfg.enable_ptz = request.enabled

    return {
        "status": "ok",
        "auto_tracking": cfg.enable_ptz,
    }


@router.post("/home")
async def ptz_home():
    """Go to home position."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    logger.info("PTZ home command received")
    success = ptz.go_home()
    return {"status": "ok" if success else "failed", "sent": success}


@router.get("/presets")
async def get_presets():
    """Get list of saved presets."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        return []

    return ptz.get_presets()


@router.post("/presets")
async def save_preset(name: str = None):
    """Save current position as a preset."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    token = ptz.set_preset(name)
    if token:
        return {"status": "ok", "token": token, "name": name}
    raise HTTPException(status_code=500, detail="Failed to save preset")


@router.post("/presets/{token}/goto")
async def goto_preset(token: str):
    """Go to a saved preset."""
    ptz = _state.get("ptz")
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Disable auto-tracking when using presets
    cfg = _state.get("config")
    if cfg:
        cfg.enable_ptz = False

    logger.info(f"PTZ goto preset: {token}")
    success = ptz.goto_preset(token)
    return {"status": "ok" if success else "failed", "sent": success}


@router.delete("/presets/{token}")
async def delete_preset(token: str):
    """Delete a saved preset."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.remove_preset(token)
    return {"status": "ok" if success else "failed"}


@router.get("/imaging")
async def get_imaging_status():
    """Get imaging status (light, night mode)."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        return {
            "ir_light": False,
            "night_mode": False,
            "imaging_available": False
        }

    return ptz.get_imaging_status()


@router.post("/light")
async def toggle_ir_light(enabled: bool = True):
    """Toggle IR light on/off."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.set_ir_light(enabled)
    return {
        "status": "ok" if success else "unsupported",
        "ir_light": enabled if success else ptz._ir_light_on
    }


@router.post("/night-mode")
async def toggle_night_mode(enabled: bool = True):
    """Toggle night mode (IR cut filter) on/off."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.set_night_mode(enabled)
    return {
        "status": "ok" if success else "unsupported",
        "night_mode": enabled if success else ptz._night_mode_on
    }


@router.post("/reset")
async def pan_tilt_reset():
    """Perform pan/tilt correction (positional reset/calibration)."""
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
