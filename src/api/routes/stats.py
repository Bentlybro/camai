"""Stats API routes for dashboard analytics."""
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from fastapi import APIRouter, HTTPException

from database import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


def parse_timestamp(ts):
    """Parse timestamp to datetime (handles string, float, datetime)."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
        # Try parsing as float string
        try:
            return datetime.fromtimestamp(float(ts))
        except (ValueError, OSError, OverflowError):
            pass
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


@router.get("")
async def get_stats():
    """Get comprehensive stats for the dashboard."""
    if not _state:
        raise HTTPException(status_code=503, detail="State not initialized")

    # Get runtime stats from _state
    runtime_stats = _state.get("stats", {})

    # Try to get stats from database first
    try:
        db = get_database()
        today_stats = db.get_today_stats()
        hourly_stats = db.get_hourly_stats()

        # Build hourly data for chart
        hourly_data = []
        for h in hourly_stats:
            hourly_data.append({
                "hour": h["hour"],
                "label": f"{h['hour']:02d}:00",
                "count": h["count"]
            })

        summary = {
            "total_events_today": today_stats.get("total_events", 0),
            "person_events": today_stats.get("person_events", 0),
            "vehicle_events": today_stats.get("vehicle_events", 0),
            "package_events": today_stats.get("package_events", 0),
        }
        detection_breakdown = {
            "person": today_stats.get("person_events", 0),
            "vehicle": today_stats.get("vehicle_events", 0),
            "package": today_stats.get("package_events", 0),
        }
    except Exception as e:
        logger.warning(f"Failed to get stats from database: {e}")
        # Fallback to in-memory calculation
        events_list = _state.get("recent_events", [])
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        person_events_today = 0
        vehicle_events_today = 0
        package_events_today = 0
        total_events_today = 0
        hourly_counts = defaultdict(int)

        for event in events_list:
            event_time = parse_timestamp(event.get("timestamp"))
            event_type = event.get("type", "")

            if event_time and event_time >= today_start:
                total_events_today += 1
                hour = event_time.hour
                hourly_counts[hour] += 1

                if "person" in event_type:
                    person_events_today += 1
                elif "vehicle" in event_type:
                    vehicle_events_today += 1
                elif "package" in event_type:
                    package_events_today += 1

        hourly_data = []
        for hour in range(24):
            hourly_data.append({
                "hour": hour,
                "label": f"{hour:02d}:00",
                "count": hourly_counts.get(hour, 0)
            })

        summary = {
            "total_events_today": total_events_today,
            "person_events": person_events_today,
            "vehicle_events": vehicle_events_today,
            "package_events": package_events_today,
        }
        detection_breakdown = {
            "person": person_events_today,
            "vehicle": vehicle_events_today,
            "package": package_events_today,
        }

    # System stats (always from runtime)
    uptime = runtime_stats.get("uptime", 0)
    fps = runtime_stats.get("fps", 0)
    inference_ms = runtime_stats.get("inference_ms", 0)
    frame_count = runtime_stats.get("frame_count", 0)
    tracked_count = runtime_stats.get("tracked_count", 0)

    return {
        "summary": summary,
        "hourly": hourly_data,
        "detection_breakdown": detection_breakdown,
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

    def is_today(e):
        ts = parse_timestamp(e.get("timestamp"))
        return ts is not None and ts >= today_start

    total_today = sum(1 for e in events_list if is_today(e))

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

    # Try to get from database first
    try:
        db = get_database()
        daily_stats = db.get_daily_stats(days=days)

        history = []
        for stat in daily_stats:
            history.append({
                "date": stat["date"],
                "person": stat.get("person_events", 0),
                "vehicle": stat.get("vehicle_events", 0),
                "package": stat.get("package_events", 0),
                "total": stat.get("total_events", 0),
            })

        return {"history": history}
    except Exception as e:
        logger.warning(f"Failed to get history from database: {e}")

    # Fallback to in-memory
    events_list = _state.get("recent_events", [])
    now = datetime.now()

    daily_counts = defaultdict(lambda: {"person": 0, "vehicle": 0, "package": 0, "total": 0})

    for event in events_list:
        event_time = parse_timestamp(event.get("timestamp"))
        if not event_time:
            continue

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

    history = []
    for i in range(days - 1, -1, -1):
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        counts = daily_counts.get(date, {"person": 0, "vehicle": 0, "package": 0, "total": 0})
        history.append({
            "date": date,
            **counts
        })

    return {"history": history}


@router.get("/alltime")
async def get_alltime_stats():
    """Get all-time statistics."""
    try:
        db = get_database()
        stats = db.get_all_time_stats()
        return stats
    except Exception as e:
        logger.warning(f"Failed to get all-time stats: {e}")
        return {"total_events": 0, "person_events": 0, "vehicle_events": 0, "package_events": 0}


@router.get("/detections")
async def get_current_detections():
    """Get currently visible/tracked objects in real-time."""
    if not _state:
        return {"detections": [], "debug": "no state"}

    events_tracker = _state.get("events")
    if not events_tracker:
        return {"detections": [], "debug": "no events tracker"}

    try:
        detections = events_tracker.get_current_detections()
        # Include debug info
        return {
            "detections": detections,
            "tracked_count": events_tracker.tracked_count,
            "parking_stats": events_tracker.parking_stats,
        }
    except Exception as e:
        logger.warning(f"Failed to get current detections: {e}")
        return {"detections": [], "debug": str(e)}
