"""Recordings API routes."""
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse, StreamingResponse

from database import get_database
from auth.dependencies import get_current_user, require_admin, require_stream_token, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

# State from main
_state = {}


def set_state(state: dict):
    """Set shared state."""
    global _state
    _state = state


@router.get("")
async def get_recordings(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    date: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD)"),
    user: CurrentUser = Depends(get_current_user),
):
    """Get list of recordings (authenticated)."""
    try:
        db = get_database()
        recordings = db.get_recordings(limit=limit, offset=offset, date=date)

        # Add formatted timestamps and sizes
        for rec in recordings:
            if rec.get("start_time"):
                dt = datetime.fromtimestamp(rec["start_time"])
                rec["date"] = dt.strftime("%Y-%m-%d")
                rec["time"] = dt.strftime("%H:%M:%S")
                rec["formatted_time"] = dt.strftime("%b %d, %Y %I:%M %p")

            if rec.get("duration"):
                mins = int(rec["duration"] // 60)
                secs = int(rec["duration"] % 60)
                rec["formatted_duration"] = f"{mins}:{secs:02d}"

            if rec.get("file_size"):
                size_mb = rec["file_size"] / 1024 / 1024
                rec["formatted_size"] = f"{size_mb:.1f} MB"

        return {
            "recordings": recordings,
            "count": len(recordings),
            "offset": offset,
            "limit": limit,
        }
    except Exception as e:
        logger.error(f"Failed to get recordings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/{recording_id}")
async def debug_recording(recording_id: int, admin: CurrentUser = Depends(require_admin)):
    """Debug endpoint to see recording path info (admin only)."""
    from pathlib import Path

    db = get_database()
    recording = db.get_recording(recording_id)

    if not recording:
        return {"error": "Recording not found in database"}

    recorder = _state.get("recorder")
    output_dir = str(recorder.output_dir) if recorder else "N/A"

    stored_path = recording.get("path", "")

    # Try various path resolutions
    results = {
        "recording_id": recording_id,
        "stored_path": stored_path,
        "output_dir": output_dir,
        "checks": []
    }

    # Check 1: Direct path
    p1 = Path(stored_path)
    results["checks"].append({
        "method": "direct",
        "path": str(p1),
        "exists": p1.exists()
    })

    # Check 2: output_dir / stored_path
    if recorder:
        p2 = recorder.output_dir / stored_path
        results["checks"].append({
            "method": "output_dir/stored",
            "path": str(p2),
            "exists": p2.exists()
        })

        # Check 3: Strip first component if it matches output_dir name
        path_obj = Path(stored_path)
        if path_obj.parts and path_obj.parts[0] == recorder.output_dir.name:
            stripped = Path(*path_obj.parts[1:])
            p3 = recorder.output_dir / stripped
            results["checks"].append({
                "method": "stripped",
                "path": str(p3),
                "exists": p3.exists()
            })

    return results


@router.get("/dates")
async def get_recording_dates(user: CurrentUser = Depends(get_current_user)):
    """Get list of dates that have recordings (authenticated)."""
    try:
        db = get_database()
        recordings = db.get_recordings(limit=1000)

        dates = set()
        for rec in recordings:
            if rec.get("start_time"):
                dt = datetime.fromtimestamp(rec["start_time"])
                dates.add(dt.strftime("%Y-%m-%d"))

        return {"dates": sorted(dates, reverse=True)}
    except Exception as e:
        logger.error(f"Failed to get recording dates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_recording_stats(user: CurrentUser = Depends(get_current_user)):
    """Get recording storage statistics (authenticated)."""
    try:
        db = get_database()
        stats = db.get_recording_stats()

        # Format sizes
        stats["formatted_size"] = f"{stats['total_size'] / 1024 / 1024 / 1024:.2f} GB"

        # Format duration
        if stats.get("total_duration"):
            hours = int(stats["total_duration"] // 3600)
            mins = int((stats["total_duration"] % 3600) // 60)
            stats["formatted_duration"] = f"{hours}h {mins}m"

        # Get recording manager stats if available
        recorder = _state.get("recorder")
        if recorder:
            storage_stats = recorder.get_storage_stats()
            stats["storage"] = storage_stats

        return stats
    except Exception as e:
        logger.error(f"Failed to get recording stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{recording_id}")
async def get_recording(recording_id: int, user: CurrentUser = Depends(get_current_user)):
    """Get a single recording by ID (authenticated)."""
    try:
        db = get_database()
        recording = db.get_recording(recording_id)

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        # Add formatted fields
        if recording.get("start_time"):
            dt = datetime.fromtimestamp(recording["start_time"])
            recording["date"] = dt.strftime("%Y-%m-%d")
            recording["time"] = dt.strftime("%H:%M:%S")
            recording["formatted_time"] = dt.strftime("%b %d, %Y %I:%M %p")

        if recording.get("duration"):
            mins = int(recording["duration"] // 60)
            secs = int(recording["duration"] % 60)
            recording["formatted_duration"] = f"{mins}:{secs:02d}"

        return recording
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{recording_id}/stream")
async def stream_recording(recording_id: int, user: CurrentUser = Depends(get_current_user)):
    """Stream recording video (authenticated)."""
    try:
        db = get_database()
        recording = db.get_recording(recording_id)

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found in database")

        # Get recording manager to find file path
        recorder = _state.get("recorder")
        if not recorder:
            raise HTTPException(status_code=500, detail="Recording system not available")

        stored_path = recording["path"]
        logger.info(f"Stream request for recording {recording_id}: stored_path='{stored_path}'")

        video_path = recorder.get_recording_path(stored_path)
        logger.info(f"Resolved video_path: {video_path}, exists: {video_path.exists() if video_path else 'None'}")

        if not video_path or not video_path.exists():
            # Try direct path as fallback
            from pathlib import Path
            direct_path = Path(stored_path)
            if direct_path.exists():
                video_path = direct_path
                logger.info(f"Using direct path: {video_path}")
            else:
                logger.error(f"Video file not found for recording {recording_id}: stored_path='{stored_path}', resolved='{video_path}'")
                raise HTTPException(status_code=404, detail=f"Video file not found: {stored_path}")

        return FileResponse(
            video_path,
            media_type="video/mp4",
            filename=recording["filename"],
            headers={
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"inline; filename=\"{recording['filename']}\"",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stream recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{recording_id}/download")
async def download_recording(recording_id: int, user: CurrentUser = Depends(get_current_user)):
    """Download recording video file (authenticated)."""
    try:
        db = get_database()
        recording = db.get_recording(recording_id)

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        recorder = _state.get("recorder")
        if not recorder:
            raise HTTPException(status_code=500, detail="Recording system not available")

        stored_path = recording["path"]
        video_path = recorder.get_recording_path(stored_path)

        if not video_path or not video_path.exists():
            # Try direct path
            from pathlib import Path
            direct_path = Path(stored_path)
            if direct_path.exists():
                video_path = direct_path
            else:
                raise HTTPException(status_code=404, detail="Video file not found")

        return FileResponse(
            video_path,
            media_type="video/mp4",
            filename=recording["filename"],
            headers={
                "Content-Disposition": f"attachment; filename=\"{recording['filename']}\"",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{recording_id}/thumbnail")
async def get_thumbnail(recording_id: int, user: CurrentUser = Depends(require_stream_token)):
    """Get recording thumbnail (requires stream token via ?token=xxx)."""
    try:
        db = get_database()
        recording = db.get_recording(recording_id)

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        recorder = _state.get("recorder")
        if not recorder:
            raise HTTPException(status_code=500, detail="Recording system not available")

        thumbnail_path = recording.get("thumbnail_path")
        if thumbnail_path:
            full_path = Path(thumbnail_path)
            if full_path.exists():
                return FileResponse(full_path, media_type="image/jpeg")

        raise HTTPException(status_code=404, detail="Thumbnail not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get thumbnail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{recording_id}")
async def delete_recording(recording_id: int, admin: CurrentUser = Depends(require_admin)):
    """Delete a recording (admin only)."""
    try:
        db = get_database()
        recording = db.get_recording(recording_id)

        if not recording:
            raise HTTPException(status_code=404, detail="Recording not found")

        # Get recording manager to delete files
        recorder = _state.get("recorder")
        if recorder:
            recorder.delete_recording(recording["path"])

        # Delete from database
        db.delete_recording(recording_id)

        return {"success": True, "message": "Recording deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete recording: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
async def cleanup_recordings(admin: CurrentUser = Depends(require_admin)):
    """Manually trigger cleanup of old recordings (admin only)."""
    try:
        recorder = _state.get("recorder")
        if recorder:
            count, size = recorder.cleanup_old_recordings()

        db = get_database()
        paths = db.cleanup_old_recordings(days_to_keep=30)

        return {
            "success": True,
            "deleted_files": count if recorder else 0,
            "deleted_bytes": size if recorder else 0,
            "deleted_records": len(paths),
        }
    except Exception as e:
        logger.error(f"Failed to cleanup recordings: {e}")
        raise HTTPException(status_code=500, detail=str(e))
