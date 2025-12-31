"""App update API routes for OTA updates."""
import os
import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app", tags=["app-update"])

# Directory where APK releases are stored
RELEASES_DIR = Path(__file__).parent.parent.parent.parent / "releases"

# Version info file
VERSION_FILE = RELEASES_DIR / "version.json"


def get_version_info():
    """Load version info from version.json."""
    if not VERSION_FILE.exists():
        return None

    try:
        with open(VERSION_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load version info: {e}")
        return None


@router.get("/version")
async def get_latest_version():
    """
    Get the latest app version info.
    Returns version number, release notes, and download URL.
    """
    version_info = get_version_info()

    if not version_info:
        # Return a default if no version file exists
        return {
            "version": "1.0.0",
            "version_code": 1,
            "release_notes": "Initial release",
            "apk_url": "/api/app/download",
            "apk_size": 0,
            "required": False,
        }

    return version_info


@router.get("/download")
async def download_apk():
    """
    Download the latest APK file.
    """
    version_info = get_version_info()

    if not version_info:
        raise HTTPException(status_code=404, detail="No release available")

    apk_filename = version_info.get("apk_filename", "app-release.apk")
    apk_path = RELEASES_DIR / apk_filename

    if not apk_path.exists():
        raise HTTPException(status_code=404, detail="APK file not found")

    return FileResponse(
        apk_path,
        media_type="application/vnd.android.package-archive",
        filename=apk_filename,
        headers={
            "Content-Disposition": f"attachment; filename={apk_filename}"
        }
    )


@router.get("/check/{current_version}")
async def check_for_update(current_version: str):
    """
    Check if an update is available for the given version.
    Returns update info if available, or null if up to date.
    """
    version_info = get_version_info()

    if not version_info:
        return {"update_available": False}

    latest_version = version_info.get("version", "1.0.0")

    # Compare versions (simple string comparison works for semver)
    def parse_version(v):
        try:
            parts = v.replace('v', '').split('.')
            return tuple(int(p) for p in parts[:3])
        except:
            return (0, 0, 0)

    current = parse_version(current_version)
    latest = parse_version(latest_version)

    if latest > current:
        return {
            "update_available": True,
            "current_version": current_version,
            "latest_version": latest_version,
            "version_code": version_info.get("version_code", 1),
            "release_notes": version_info.get("release_notes", ""),
            "apk_url": "/api/app/download",
            "apk_size": version_info.get("apk_size", 0),
            "required": version_info.get("required", False),
        }

    return {"update_available": False, "current_version": current_version}
