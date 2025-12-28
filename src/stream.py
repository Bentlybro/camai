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
        elif self.path == "/face":
            self._stream_face()
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

    def _stream_face(self):
        self.send_response(200)
        self.send_header("Content-type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while True:
                frame = self.server.stream.get_face_frame()
                if frame is None:
                    # No face - send the regular frame as fallback
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
    """MJPEG stream server with face zoom stream."""

    def __init__(self, port: int = 8080, quality: int = 70):
        self.port = port
        self.quality = quality
        self._frame = None
        self._face_frame = None
        self._lock = threading.Lock()
        self._server = None

    def update(self, frame: np.ndarray):
        """Update current frame."""
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
        # No longer starts own server - FastAPI handles HTTP
        pass

    def stop(self):
        # No longer manages server
        pass


def annotate_frame(
    frame: np.ndarray,
    detections: list,
    fps: float = 0,
    inference_ms: float = 0,
    keypoints_list: list = None,
) -> np.ndarray:
    """Draw boxes, skeletons, and stats on frame."""
    import cv2

    out = frame.copy()
    colors = {"person": (0,255,0), "car": (255,0,0), "truck": (255,128,0), "package": (0,255,255)}

    for d in detections:
        x1, y1, x2, y2 = d.bbox
        color = colors.get(d.class_name, (128,128,128))
        cv2.rectangle(out, (x1,y1), (x2,y2), color, 2)

        label = f"{d.class_name.upper()} {d.confidence:.0%}"
        cv2.putText(out, label, (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # Draw pose skeletons if available
    if keypoints_list:
        out = draw_skeletons(out, keypoints_list)

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


# Body skeleton connections (no face - indices 5-16 only)
# Format: (start_keypoint, end_keypoint)
BODY_SKELETON = [
    (5, 6),   # shoulders
    (5, 7),   # left shoulder - left elbow
    (7, 9),   # left elbow - left wrist
    (6, 8),   # right shoulder - right elbow
    (8, 10),  # right elbow - right wrist
    (5, 11),  # left shoulder - left hip
    (6, 12),  # right shoulder - right hip
    (11, 12), # hips
    (11, 13), # left hip - left knee
    (13, 15), # left knee - left ankle
    (12, 14), # right hip - right knee
    (14, 16), # right knee - right ankle
]


def draw_skeletons(frame: np.ndarray, keypoints_list: list) -> np.ndarray:
    """Draw body skeletons on frame (no face keypoints)."""
    import cv2

    skeleton_color = (0, 255, 255)  # Yellow
    joint_color = (0, 165, 255)     # Orange

    for keypoints in keypoints_list:
        if keypoints is None or len(keypoints) < 17:
            continue

        # Draw skeleton lines (body only)
        for start_idx, end_idx in BODY_SKELETON:
            if start_idx >= len(keypoints) or end_idx >= len(keypoints):
                continue

            x1, y1, conf1 = keypoints[start_idx]
            x2, y2, conf2 = keypoints[end_idx]

            # Only draw if both points are confident enough
            if conf1 > 0.5 and conf2 > 0.5:
                cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), skeleton_color, 2)

        # Draw joints (body only - indices 5-16)
        for i in range(5, min(17, len(keypoints))):
            x, y, conf = keypoints[i]
            if conf > 0.5:
                cv2.circle(frame, (int(x), int(y)), 4, joint_color, -1)

    return frame


def extract_face_crop(
    frame: np.ndarray,
    detections: list,
    keypoints_list: list = None,
    output_size: tuple = (480, 480),
    padding: float = 0.5,
) -> np.ndarray:
    """
    Extract and zoom into the face of the largest detected person.

    Args:
        frame: Original frame
        detections: List of detections
        keypoints_list: Optional pose keypoints for better face localization
        output_size: Size of the output cropped image
        padding: Extra padding around face (0.5 = 50% extra on each side)

    Returns:
        Cropped and resized face image, or None if no person detected
    """
    import cv2

    # Filter for people only
    people = [d for d in detections if d.class_name == "person"]
    if not people:
        return None

    # Find largest person (closest to camera)
    largest = max(people, key=lambda d: (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]))
    x1, y1, x2, y2 = largest.bbox

    h, w = frame.shape[:2]
    person_width = x2 - x1
    person_height = y2 - y1

    # Try to find face from keypoints (nose, eyes)
    face_cx, face_cy = None, None

    if keypoints_list and len(keypoints_list) > 0:
        # Find keypoints for the largest person (approximate by position)
        for kpts in keypoints_list:
            if len(kpts) >= 5:
                # Check if nose (idx 0) is within person bbox
                nose_x, nose_y, nose_conf = kpts[0]
                if x1 <= nose_x <= x2 and y1 <= nose_y <= y2 and nose_conf > 0.5:
                    face_cx, face_cy = nose_x, nose_y
                    break

    # If no keypoints, estimate face as top 25% of person bbox
    if face_cx is None:
        face_cx = (x1 + x2) / 2
        face_cy = y1 + person_height * 0.15  # Near top of bbox

    # Estimate face size (roughly 1/7 of body height for full body, larger if closer)
    face_size = max(person_width * 0.8, person_height * 0.25)

    # Add padding
    face_size_padded = face_size * (1 + padding)
    half_size = face_size_padded / 2

    # Calculate crop region
    crop_x1 = int(max(0, face_cx - half_size))
    crop_y1 = int(max(0, face_cy - half_size))
    crop_x2 = int(min(w, face_cx + half_size))
    crop_y2 = int(min(h, face_cy + half_size))

    # Make sure we have a valid crop
    if crop_x2 - crop_x1 < 20 or crop_y2 - crop_y1 < 20:
        return None

    # Crop the face region
    face_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]

    # Resize to output size
    face_resized = cv2.resize(face_crop, output_size, interpolation=cv2.INTER_LINEAR)

    # Add label
    cv2.putText(face_resized, "FACE ZOOM", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    return face_resized
