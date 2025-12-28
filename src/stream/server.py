"""MJPEG stream server for viewing detections."""
import threading
import numpy as np


class StreamServer:
    """MJPEG stream server with face zoom stream."""

    def __init__(self, port: int = 8080, quality: int = 65):
        self.port = port
        self.quality = quality  # Reduced from 70 for better performance
        self._frame = None
        self._face_frame = None
        self._lock = threading.Lock()

    def update(self, frame: np.ndarray):
        """Update current frame (annotated)."""
        import cv2
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        with self._lock:
            self._frame = buf.tobytes()

    def update_face(self, frame: np.ndarray):
        """Update face zoom frame."""
        import cv2
        if frame is None:
            with self._lock:
                self._face_frame = None
            return
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        with self._lock:
            self._face_frame = buf.tobytes()

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame

    def get_face_frame(self) -> bytes:
        with self._lock:
            return self._face_frame

    def start(self):
        """No longer starts own server - FastAPI handles HTTP."""
        pass

    def stop(self):
        """No longer manages server."""
        pass
