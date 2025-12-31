"""FastAPI app setup and state management."""
import asyncio
import json
import logging
import base64
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .routes import settings, ptz, events, streams, stats, system, recordings
from database import get_database

logger = logging.getLogger(__name__)

# Global for async event loop reference (set when WebSocket connects)
_event_loop: Optional[asyncio.AbstractEventLoop] = None

app = FastAPI(title="CAMAI", description="Jetson AI Camera System")

# Add CORS middleware to allow mobile app access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local network access
    allow_credentials=False,  # Must be False when using wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    # Update route modules with new state
    settings.set_state(_state)
    ptz.set_state(_state)
    events.set_state(_state)
    streams.set_state(_state)
    stats.set_state(_state)
    recordings.set_state(_state)


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
    """Add event to recent events list and database."""
    _state["recent_events"].insert(0, event_dict)
    _state["recent_events"] = _state["recent_events"][:100]

    # Save to database
    try:
        db = get_database()
        db.add_event(event_dict)
    except Exception as e:
        logger.warning(f"Failed to save event to database: {e}")


async def _broadcast_to_clients(message: dict):
    """Broadcast message to all connected WebSocket clients."""
    if not _ws_connections:
        return

    message_text = json.dumps(message)
    disconnected = set()

    for ws in _ws_connections:
        try:
            await ws.send_text(message_text)
        except Exception:
            disconnected.add(ws)

    # Clean up disconnected clients
    for ws in disconnected:
        _ws_connections.discard(ws)


def broadcast_alert(alert_data: dict):
    """
    Broadcast a person detection alert to all connected clients.
    Called from RecordingManager when person detected.

    alert_data should contain:
    - type: "person_alert"
    - timestamp: Unix timestamp
    - screenshot: JPEG bytes
    - detections: list of detection info
    """
    global _event_loop

    if not _ws_connections:
        return

    # Convert screenshot to base64 for JSON transport
    message = {
        "type": "person_alert",
        "timestamp": alert_data.get("timestamp"),
        "detections": alert_data.get("detections", []),
    }

    # Encode screenshot as base64 if present
    if alert_data.get("screenshot"):
        message["screenshot"] = base64.b64encode(alert_data["screenshot"]).decode("utf-8")

    # Schedule broadcast on the event loop
    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_to_clients(message), _event_loop)
    else:
        logger.debug("No event loop available for broadcast")


# Initialize route state
settings.set_state(_state)
ptz.set_state(_state)
events.set_state(_state)
streams.set_state(_state)
stats.set_state(_state)
recordings.set_state(_state)

# Include routers
app.include_router(settings.router)
app.include_router(ptz.router)
app.include_router(events.router)
app.include_router(streams.router)
app.include_router(stats.router)
app.include_router(system.router)
app.include_router(recordings.router)


@app.get("/")
async def root():
    """Serve dashboard."""
    web_dir = Path(__file__).parent.parent.parent / "web"
    return FileResponse(web_dir / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates."""
    global _event_loop

    # Capture event loop for broadcasting from other threads
    _event_loop = asyncio.get_event_loop()

    await websocket.accept()
    _ws_connections.add(websocket)
    logger.info(f"WebSocket connected. Total: {len(_ws_connections)}")

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    finally:
        _ws_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(_ws_connections)}")


# Mount static files
web_dir = Path(__file__).parent.parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=web_dir), name="static")
