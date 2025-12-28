"""Video stream API routes."""
import time
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

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
                # No face detected - use main stream as fallback
                frame = stream_server.get_frame()
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(1/30)


@router.get("/stream")
async def video_stream():
    """Main video stream."""
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@router.get("/face")
async def face_stream():
    """Face zoom stream."""
    return StreamingResponse(
        generate_face_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
