"""MJPEG stream server for viewing detections."""
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime
import numpy as np
import logging

logger = logging.getLogger(__name__)


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Suppress logs

    def do_GET(self):
        if self.path in ("/", "/stream"):
            self._stream()
        elif self.path == "/snapshot":
            self._snapshot()
        else:
            self.send_error(404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while True:
                frame = self.server.stream.get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue

                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(1/30)
        except:
            pass

    def _snapshot(self):
        frame = self.server.stream.get_frame()
        if frame:
            self.send_response(200)
            self.send_header("Content-type", "image/jpeg")
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_error(503)


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class StreamServer:
    """MJPEG stream server."""

    def __init__(self, port: int = 8080, quality: int = 70):
        self.port = port
        self.quality = quality
        self._frame = None
        self._lock = threading.Lock()
        self._server = None

    def update(self, frame: np.ndarray):
        """Update current frame."""
        import cv2
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        with self._lock:
            self._frame = buf.tobytes()

    def get_frame(self) -> bytes:
        with self._lock:
            return self._frame

    def start(self):
        self._server = ThreadedServer(("0.0.0.0", self.port), StreamHandler)
        self._server.stream = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        logger.info(f"Stream: http://0.0.0.0:{self.port}/stream")

    def stop(self):
        if self._server:
            self._server.shutdown()


def annotate_frame(
    frame: np.ndarray,
    detections: list,
    fps: float = 0,
    inference_ms: float = 0,
) -> np.ndarray:
    """Draw boxes and stats on frame."""
    import cv2

    out = frame.copy()
    colors = {"person": (0,255,0), "car": (255,0,0), "truck": (255,128,0), "package": (0,255,255)}

    for d in detections:
        x1, y1, x2, y2 = d.bbox
        color = colors.get(d.class_name, (128,128,128))
        cv2.rectangle(out, (x1,y1), (x2,y2), color, 2)

        label = f"{d.class_name.upper()} {d.confidence:.0%}"
        cv2.putText(out, label, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # Stats overlay (top right)
    h, w = out.shape[:2]

    stats_text = f"FPS: {fps:.1f} | Inf: {inference_ms:.1f}ms"
    (tw, th), _ = cv2.getTextSize(stats_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
    cv2.putText(out, stats_text, (w - tw - 10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    time_text = datetime.now().strftime("%H:%M:%S")
    (tw2, _), _ = cv2.getTextSize(time_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(out, time_text, (w - tw2 - 10, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)

    return out
