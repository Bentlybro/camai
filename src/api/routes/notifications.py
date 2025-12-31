"""Notification API routes."""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# State from main
_state = {}


def set_state(state: dict):
    """Set shared state."""
    global _state
    _state = state


class RegisterTokenRequest(BaseModel):
    """Request to register FCM token."""
    token: str
    device_name: Optional[str] = ""
    platform: Optional[str] = "android"


class UnregisterTokenRequest(BaseModel):
    """Request to unregister FCM token."""
    token: str


class TestNotificationRequest(BaseModel):
    """Request to send a test notification."""
    title: Optional[str] = "Test Notification"
    body: Optional[str] = "This is a test notification from CAMAI"


@router.post("/register")
async def register_device(request: RegisterTokenRequest):
    """
    Register a device for push notifications.

    The client should call this with their FCM token after initializing Firebase.
    """
    firebase = _state.get("firebase")
    if not firebase:
        raise HTTPException(status_code=503, detail="Firebase not initialized")

    if not firebase.initialized:
        raise HTTPException(status_code=503, detail="Firebase credentials not configured")

    success = firebase.register_token(
        token=request.token,
        device_name=request.device_name,
        platform=request.platform,
    )

    if success:
        return {
            "success": True,
            "message": f"Device registered: {request.device_name or 'Unknown'}",
        }
    else:
        raise HTTPException(status_code=400, detail="Failed to register device")


@router.post("/unregister")
async def unregister_device(request: UnregisterTokenRequest):
    """Unregister a device from push notifications."""
    firebase = _state.get("firebase")
    if not firebase:
        raise HTTPException(status_code=503, detail="Firebase not initialized")

    success = firebase.unregister_token(request.token)

    return {
        "success": success,
        "message": "Device unregistered" if success else "Device not found",
    }


@router.get("/devices")
async def get_registered_devices():
    """Get list of registered devices."""
    firebase = _state.get("firebase")
    if not firebase:
        return {"devices": [], "firebase_initialized": False}

    return {
        "devices": firebase.get_registered_devices(),
        "firebase_initialized": firebase.initialized,
        "device_count": len(firebase.get_registered_devices()),
    }


@router.post("/test")
async def send_test_notification(request: TestNotificationRequest):
    """Send a test notification to all registered devices."""
    firebase = _state.get("firebase")
    if not firebase:
        raise HTTPException(status_code=503, detail="Firebase not initialized")

    if not firebase.initialized:
        raise HTTPException(status_code=503, detail="Firebase credentials not configured")

    result = firebase.send_notification(
        title=request.title,
        body=request.body,
        data={"type": "test"},
    )

    return {
        "success": result.get("success", 0) > 0,
        "sent": result.get("success", 0),
        "failed": result.get("failure", 0),
        "message": f"Sent to {result.get('success', 0)} devices",
    }


@router.get("/status")
async def get_notification_status():
    """Get Firebase notification service status."""
    firebase = _state.get("firebase")

    if not firebase:
        return {
            "available": False,
            "initialized": False,
            "error": "Firebase service not created",
        }

    return {
        "available": True,
        "initialized": firebase.initialized,
        "registered_devices": len(firebase.get_registered_devices()),
    }
