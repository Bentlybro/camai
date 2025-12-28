"""FastAPI backend for CAMAI web dashboard."""
import asyncio
import json
import logging
import os
import time
import threading
from pathlib import Path
from typing import Optional
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="CAMAI", description="Jetson AI Camera System")

# Global state (set by main)
_state = {
    "config": None,
    "detector": None,
    "capture": None,
    "events": None,
    "ptz": None,
    "pose": None,
    "stream_server": None,
    "stats": {
        "fps": 0,
        "inference_ms": 0,
        "frame_count": 0,
        "tracked_objects": 0,
        "uptime": 0,
    },
    "recent_events": [],
}

# WebSocket connections for real-time updates
_ws_connections = set()


def set_state(key: str, value):
    """Set global state from main."""
    _state[key] = value


def get_state(key: str):
    """Get global state."""
    return _state.get(key)


def update_stats(fps: float, inference_ms: float, frame_count: int, tracked: int, uptime: float):
    """Update stats from main loop."""
    _state["stats"] = {
        "fps": round(fps, 1),
        "inference_ms": round(inference_ms, 1),
        "frame_count": frame_count,
        "tracked_objects": tracked,
        "uptime": round(uptime, 1),
    }


def add_event(event_dict: dict):
    """Add event to recent events list."""
    _state["recent_events"].insert(0, event_dict)
    # Keep only last 100 events
    _state["recent_events"] = _state["recent_events"][:100]


async def broadcast_stats():
    """Broadcast stats to all WebSocket connections."""
    if not _ws_connections:
        return

    message = json.dumps({
        "type": "stats",
        "data": _state["stats"]
    })

    dead_connections = set()
    for ws in _ws_connections:
        try:
            await ws.send_text(message)
        except:
            dead_connections.add(ws)

    _ws_connections.difference_update(dead_connections)


# ============== Settings Models ==============

class DetectionSettings(BaseModel):
    confidence: float = 0.5
    iou_threshold: float = 0.45


class PTZSettings(BaseModel):
    enabled: bool = False
    track_speed: float = 0.5
    deadzone: float = 0.15


class PoseSettings(BaseModel):
    enabled: bool = False


class StreamSettings(BaseModel):
    quality: int = 70
    width: int = 640
    height: int = 480


class AllSettings(BaseModel):
    detection: DetectionSettings
    ptz: PTZSettings
    pose: PoseSettings
    stream: StreamSettings


# ============== API Routes ==============

@app.get("/")
async def root():
    """Serve dashboard."""
    web_dir = Path(__file__).parent.parent / "web"
    return FileResponse(web_dir / "index.html")


@app.get("/api/stats")
async def get_stats():
    """Get current system stats."""
    return _state["stats"]


@app.get("/api/settings")
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
        },
        "pose": {
            "enabled": cfg.enable_pose,
        },
        "stream": {
            "quality": 70,
            "width": cfg.capture_width,
            "height": cfg.capture_height,
        }
    }


@app.post("/api/settings/detection")
async def update_detection_settings(settings: DetectionSettings):
    """Update detection settings."""
    cfg = _state["config"]
    detector = _state["detector"]

    if cfg:
        cfg.confidence = settings.confidence
        cfg.iou_threshold = settings.iou_threshold

    if detector:
        detector.confidence = settings.confidence
        detector.iou_threshold = settings.iou_threshold

    logger.info(f"Updated detection: conf={settings.confidence}, iou={settings.iou_threshold}")
    return {"status": "ok"}


@app.post("/api/settings/ptz")
async def update_ptz_settings(settings: PTZSettings):
    """Update PTZ settings."""
    cfg = _state["config"]
    ptz = _state["ptz"]

    if cfg:
        cfg.enable_ptz = settings.enabled
        cfg.ptz_track_speed = settings.track_speed
        cfg.ptz_deadzone = settings.deadzone

    if ptz and ptz.config:
        ptz.config.track_speed = settings.track_speed
        ptz.config.deadzone = settings.deadzone

    logger.info(f"Updated PTZ: enabled={settings.enabled}, speed={settings.track_speed}")
    return {"status": "ok"}


@app.post("/api/settings/pose")
async def update_pose_settings(settings: PoseSettings):
    """Update pose settings."""
    cfg = _state["config"]

    if cfg:
        cfg.enable_pose = settings.enabled

    logger.info(f"Updated pose: enabled={settings.enabled}")
    return {"status": "ok", "note": "Restart required for pose model changes"}


@app.post("/api/settings/stream")
async def update_stream_settings(settings: StreamSettings):
    """Update stream/resolution settings."""
    cfg = _state["config"]

    if cfg:
        cfg.capture_width = settings.width
        cfg.capture_height = settings.height

    logger.info(f"Updated stream: {settings.width}x{settings.height}")
    return {"status": "ok", "note": "Restart required for resolution changes"}


# ============== PTZ Control ==============

class PTZMoveRequest(BaseModel):
    pan: float = 0  # -1.0 (left) to 1.0 (right)
    tilt: float = 0  # -1.0 (down) to 1.0 (up)


@app.post("/api/ptz/move")
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


@app.post("/api/ptz/stop")
async def ptz_stop():
    """Stop PTZ movement."""
    ptz = _state["ptz"]
    if not ptz or not ptz._connected:
        raise HTTPException(status_code=503, detail="PTZ not connected")

    ptz._is_moving = True  # Force stop to send command
    ptz.stop()
    return {"status": "ok"}


@app.get("/api/ptz/status")
async def ptz_status():
    """Get PTZ connection status."""
    ptz = _state["ptz"]
    cfg = _state["config"]

    return {
        "connected": ptz is not None and ptz._connected if ptz else False,
        "auto_tracking": cfg.enable_ptz if cfg else False,
    }


@app.get("/api/events")
async def get_events(limit: int = 50):
    """Get recent events."""
    return _state["recent_events"][:limit]


@app.get("/api/snapshots")
async def list_snapshots():
    """List saved snapshots."""
    cfg = _state["config"]
    if not cfg:
        return []

    snapshot_dir = Path(cfg.snapshot_dir)
    if not snapshot_dir.exists():
        return []

    snapshots = []
    for f in sorted(snapshot_dir.glob("*.jpg"), reverse=True)[:50]:
        snapshots.append({
            "filename": f.name,
            "path": f"/api/snapshots/{f.name}",
            "timestamp": f.stat().st_mtime,
        })

    return snapshots


@app.get("/api/snapshots/{filename}")
async def get_snapshot(filename: str):
    """Get a specific snapshot."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not loaded")

    filepath = Path(cfg.snapshot_dir) / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(filepath, media_type="image/jpeg")


# ============== WebSocket ==============

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates."""
    await websocket.accept()
    _ws_connections.add(websocket)
    logger.info(f"WebSocket connected. Total: {len(_ws_connections)}")

    try:
        while True:
            # Keep connection alive, handle incoming messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Handle ping/pong or commands
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send ping to keep alive
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(_ws_connections)}")


# ============== Video Streams ==============

def generate_mjpeg():
    """Generate MJPEG stream."""
    stream_server = _state["stream_server"]
    while True:
        if stream_server:
            frame = stream_server.get_frame()
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(1/30)


def generate_face_mjpeg():
    """Generate face zoom MJPEG stream."""
    stream_server = _state["stream_server"]
    while True:
        if stream_server:
            frame = stream_server.get_face_frame()
            if frame is None:
                frame = stream_server.get_frame()
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(1/30)


@app.get("/stream")
async def video_stream():
    """Main video stream."""
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/face")
async def face_stream():
    """Face zoom stream."""
    return StreamingResponse(
        generate_face_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# Mount static files
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=web_dir), name="static")
