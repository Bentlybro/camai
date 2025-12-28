"""MJPEG stream server for viewing detections."""
import threading
import numpy as np


class StreamServer:
    """MJPEG stream server."""

    def __init__(self, port: int = 8080, quality: int = 65):
        self.port = port
        self.quality = quality
        self._frame = None
        self._lock = threading.Lock()

    def update(self, frame: np.ndarray):
        """Update current frame."""
        import cv2
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        with self._lock:
            self._frame = buf.tobytes()

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame
