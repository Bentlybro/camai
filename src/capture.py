"""RTSP video capture with hardware decode on Jetson."""
import threading
import time
import logging
from queue import Queue, Empty
import numpy as np

logger = logging.getLogger(__name__)


class RTSPCapture:
    """RTSP capture with GStreamer hardware decode."""

    def __init__(self, rtsp_url: str, width: int = 640, height: int = 480):
        self.url = rtsp_url
        self.width = width
        self.height = height
        self._cap = None
        self._frame = None
        self._running = False
        self._connected = False
        self._thread = None
        self._lock = threading.Lock()
        self._fps = 0

    def _gst_pipeline(self) -> str:
        """GStreamer pipeline for Jetson hardware decode."""
        return (
            f"rtspsrc location={self.url} latency=50 drop-on-latency=true ! "
            f"rtph264depay ! h264parse ! nvv4l2decoder ! "
            f"nvvidconv ! video/x-raw,width={self.width},height={self.height},format=BGRx ! "
            f"videoconvert ! video/x-raw,format=BGR ! appsink drop=1 max-buffers=1"
        )

    def start(self):
        """Start capture thread."""
        import cv2

        # Try GStreamer, fallback to OpenCV
        try:
            self._cap = cv2.VideoCapture(self._gst_pipeline(), cv2.CAP_GSTREAMER)
            if self._cap.isOpened():
                logger.info("Using GStreamer hardware decode")
            else:
                raise RuntimeError("GStreamer failed")
        except Exception:
            logger.info("Using OpenCV decode")
            self._cap = cv2.VideoCapture(self.url)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._connected = self._cap.isOpened()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        """Capture loop."""
        frame_count = 0
        start = time.time()

        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
                    self._connected = True
                frame_count += 1

                # Update FPS every second
                if frame_count % 30 == 0:
                    self._fps = frame_count / (time.time() - start)
            else:
                self._connected = False
                time.sleep(0.1)

    def read(self) -> np.ndarray:
        """Get latest frame."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        """Stop capture."""
        self._running = False
        if self._cap:
            self._cap.release()

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def is_connected(self) -> bool:
        return self._connected

    def restart(self, width: int = None, height: int = None):
        """Restart capture with optional new resolution."""
        if width:
            self.width = width
        if height:
            self.height = height

        self.stop()
        if self._thread:
            self._thread.join(timeout=2)
        self._frame = None
        time.sleep(0.5)
        self.start()
