"""Stats API routes for dashboard analytics."""
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


@router.get("")
async def get_stats():
    """Get comprehensive stats for the dashboard."""
    if not _state:
        raise HTTPException(status_code=503, detail="State not initialized")

    # Get runtime stats from _state
    runtime_stats = _state.get("stats", {})
    events_list = _state.get("recent_events", [])

    # Calculate summary stats
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count events by type today
    person_events_today = 0
    vehicle_events_today = 0
    package_events_today = 0
    total_events_today = 0

    # Hourly breakdown for today (last 24 hours)
    hourly_counts = defaultdict(int)

    # Detection type breakdown
    type_counts = defaultdict(int)

    for event in events_list:
        event_time = event.get("timestamp")
        event_type = event.get("type", "")

        # Parse timestamp if it's a string
        if isinstance(event_time, str):
            try:
                event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
            except:
                continue

        # Check if event is from today
        if event_time and event_time >= today_start:
            total_events_today += 1

            if "person" in event_type:
                person_events_today += 1
                type_counts["person"] += 1
            elif "vehicle" in event_type:
                vehicle_events_today += 1
                type_counts["vehicle"] += 1
            elif "package" in event_type:
                package_events_today += 1
                type_counts["package"] += 1

            # Hourly breakdown
            hour = event_time.hour
            hourly_counts[hour] += 1

    # Build hourly data for chart (24 hours)
    hourly_data = []
    for hour in range(24):
        hourly_data.append({
            "hour": hour,
            "label": f"{hour:02d}:00",
            "count": hourly_counts.get(hour, 0)
        })

    # System stats
    uptime = runtime_stats.get("uptime", 0)
    fps = runtime_stats.get("fps", 0)
    inference_ms = runtime_stats.get("inference_ms", 0)
    frame_count = runtime_stats.get("frame_count", 0)
    tracked_count = runtime_stats.get("tracked_count", 0)

    return {
        "summary": {
            "total_events_today": total_events_today,
            "person_events": person_events_today,
            "vehicle_events": vehicle_events_today,
            "package_events": package_events_today,
        },
        "hourly": hourly_data,
        "detection_breakdown": {
            "person": type_counts.get("person", 0),
            "vehicle": type_counts.get("vehicle", 0),
            "package": type_counts.get("package", 0),
        },
        "system": {
            "uptime_seconds": uptime,
            "uptime_formatted": format_uptime(uptime),
            "fps": round(fps, 1),
            "inference_ms": round(inference_ms, 1),
            "frame_count": frame_count,
            "tracked_objects": tracked_count,
        }
    }


@router.get("/summary")
async def get_summary():
    """Get quick summary stats."""
    if not _state:
        raise HTTPException(status_code=503, detail="State not initialized")

    runtime_stats = _state.get("stats", {})
    events_list = _state.get("recent_events", [])

    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_today = sum(1 for e in events_list if parse_timestamp(e.get("timestamp")) >= today_start)

    return {
        "events_today": total_today,
        "fps": round(runtime_stats.get("fps", 0), 1),
        "uptime": format_uptime(runtime_stats.get("uptime", 0)),
        "tracked": runtime_stats.get("tracked_count", 0),
    }


@router.get("/history")
async def get_history(days: int = 7):
    """Get event history for the past N days."""
    if not _state:
        raise HTTPException(status_code=503, detail="State not initialized")

    events_list = _state.get("recent_events", [])
    now = datetime.now()

    # Daily counts for past N days
    daily_counts = defaultdict(lambda: {"person": 0, "vehicle": 0, "package": 0, "total": 0})

    for event in events_list:
        event_time = parse_timestamp(event.get("timestamp"))
        if not event_time:
            continue

        # Check if within range
        days_ago = (now - event_time).days
        if days_ago < days:
            date_str = event_time.strftime("%Y-%m-%d")
            event_type = event.get("type", "")

            daily_counts[date_str]["total"] += 1
            if "person" in event_type:
                daily_counts[date_str]["person"] += 1
            elif "vehicle" in event_type:
                daily_counts[date_str]["vehicle"] += 1
            elif "package" in event_type:
                daily_counts[date_str]["package"] += 1

    # Build response with all days (even if 0 events)
    history = []
    for i in range(days - 1, -1, -1):
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        counts = daily_counts.get(date, {"person": 0, "vehicle": 0, "package": 0, "total": 0})
        history.append({
            "date": date,
            **counts
        })

    return {"history": history}


def parse_timestamp(ts):
    """Parse timestamp string to datetime."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except:
        return None


def format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        return f"{hours}h {mins}m"
