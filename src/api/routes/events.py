"""Events and snapshots API routes."""
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from database import get_database

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


@router.get("/api/events")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: str = None,
    since: float = None
):
    """Get events from database with optional filtering."""
    try:
        db = get_database()
        events = db.get_events(limit=limit, offset=offset, event_type=event_type, since=since)
        return events
    except Exception as e:
        logger.warning(f"Failed to query events from database: {e}")
        # Fallback to in-memory events
        return _state["recent_events"][:limit]


@router.get("/api/events/count")
async def get_event_count(since: float = None):
    """Get total event count."""
    try:
        db = get_database()
        count = db.get_event_count(since=since)
        return {"count": count}
    except Exception as e:
        logger.warning(f"Failed to get event count: {e}")
        return {"count": len(_state["recent_events"])}


@router.get("/api/snapshots")
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


@router.get("/api/snapshots/{filename}")
async def get_snapshot(filename: str):
    """Get a specific snapshot."""
    cfg = _state["config"]
    if not cfg:
        raise HTTPException(status_code=503, detail="Config not loaded")

    filepath = Path(cfg.snapshot_dir) / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(filepath, media_type="image/jpeg")


