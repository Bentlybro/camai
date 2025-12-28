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
