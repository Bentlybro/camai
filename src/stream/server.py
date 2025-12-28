"""MJPEG stream server for viewing detections."""
import threading
import numpy as np


class StreamServer:
    """MJPEG stream server."""

    def __init__(self, port: int = 8080, quality: int = 65):
        self.port = port
        self.quality = quality
        self._frame = None
        self._clean_frame = None
        self._lock = threading.Lock()

    def update(self, frame: np.ndarray, clean_frame: np.ndarray = None):
        """Update current frame and optionally clean frame."""
        import cv2
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        with self._lock:
            self._frame = buf.tobytes()
            if clean_frame is not None:
                _, clean_buf = cv2.imencode('.jpg', clean_frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                self._clean_frame = clean_buf.tobytes()

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame

    def get_clean_frame(self) -> bytes:
        with self._lock:
            return self._clean_frame
