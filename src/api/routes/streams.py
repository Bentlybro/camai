"""Video stream API routes."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from auth.dependencies import require_stream_token, CurrentUser

router = APIRouter(tags=["streams"])

# Reference to global state (set by app.py)
_state = None

def set_state(state: dict):
    global _state
    _state = state


def generate_mjpeg():
    """Generate MJPEG stream."""
    stream_server = _state["stream_server"]
    while True:
        if stream_server:
            # Wait for new frame (event-based, no polling)
            stream_server.wait_for_frame(timeout=0.1)
            frame = stream_server.get_frame()
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


def generate_clean_mjpeg():
    """Generate clean MJPEG stream without AI overlays."""
    stream_server = _state["stream_server"]
    while True:
        if stream_server:
            # Wait for new frame (event-based, no polling)
            stream_server.wait_for_frame(timeout=0.1)
            frame = stream_server.get_clean_frame()
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


@router.get("/stream")
async def video_stream(user: CurrentUser = Depends(require_stream_token)):
    """Main video stream (requires stream token via ?token=xxx)."""
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.get("/clean-stream")
async def clean_video_stream(user: CurrentUser = Depends(require_stream_token)):
    """Clean video stream without AI overlays (requires stream token via ?token=xxx)."""
    return StreamingResponse(
        generate_clean_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
