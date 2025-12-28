"""PTZ control API routes."""
import logging
from fastapi import APIRouter, HTTPException

from ..models import PTZMoveRequest

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
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Temporarily disable auto-tracking while manually controlling
    cfg = _state["config"]
    if cfg:
        cfg.enable_ptz = False

    ptz.move(request.pan, request.tilt)
    return {"status": "ok", "pan": request.pan, "tilt": request.tilt}


@router.post("/stop")
async def ptz_stop():
    """Stop PTZ movement."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

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


@router.post("/home")
async def ptz_home():
    """Go to home position."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.go_home()
    return {"status": "ok" if success else "failed"}


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
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    # Disable auto-tracking when using presets
    cfg = _state["config"]
    if cfg:
        cfg.enable_ptz = False

    success = ptz.goto_preset(token)
    return {"status": "ok" if success else "failed"}


@router.delete("/presets/{token}")
async def delete_preset(token: str):
    """Delete a saved preset."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    success = ptz.remove_preset(token)
    return {"status": "ok" if success else "failed"}
