"""MJPEG stream server for viewing detections."""
import threading
from queue import Queue, Empty
import numpy as np


class StreamServer:
    """MJPEG stream server with async encoding."""

    def __init__(self, port: int = 8080, quality: int = 65):
        self.port = port
        self.quality = quality
        self._frame = None
        self._clean_frame = None
        self._lock = threading.Lock()
        self._frame_ready = threading.Event()  # Signal when new frame encoded
        # Async encoding
        self._encode_queue = Queue(maxsize=2)
        self._running = True
        self._encoder_thread = threading.Thread(target=self._encoder_loop, daemon=True)
        self._encoder_thread.start()

    def _encoder_loop(self):
        """Background thread for JPEG encoding."""
        import cv2
        while self._running:
            try:
                frame, clean_frame = self._encode_queue.get(timeout=0.1)
                # Encode frames
                _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                encoded = buf.tobytes()

                clean_encoded = None
                if clean_frame is not None:
                    _, clean_buf = cv2.imencode('.jpg', clean_frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
                    clean_encoded = clean_buf.tobytes()

                with self._lock:
                    self._frame = encoded
                    if clean_encoded:
                        self._clean_frame = clean_encoded
                self._frame_ready.set()  # Signal new frame available
            except Empty:
                continue

    def update(self, frame: np.ndarray, clean_frame: np.ndarray = None):
        """Queue frame for async encoding (non-blocking)."""
        # Drop frame if queue is full (don't block main loop)
        try:
            self._encode_queue.put_nowait((frame.copy(), clean_frame.copy() if clean_frame is not None else None))
        except:
            pass  # Drop frame if behind

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame

    def get_clean_frame(self) -> bytes:
        with self._lock:
            return self._clean_frame

    def wait_for_frame(self, timeout: float = 0.1) -> bool:
        """Wait for a new frame to be available."""
        if self._frame_ready.wait(timeout=timeout):
            self._frame_ready.clear()
            return True
        return False

    def stop(self):
        self._running = False
        self._frame_ready.set()  # Unblock any waiters
