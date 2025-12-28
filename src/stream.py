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


class FaceTracker:
    """Tracks faces with smoothing to prevent flickering."""

    def __init__(self, smoothing: float = 0.7, persistence_frames: int = 15):
        self.smoothing = smoothing  # 0-1, higher = smoother but more lag
        self.persistence_frames = persistence_frames
        self._tracked_faces = {}  # id -> {cx, cy, size, frames_missing}
        self._next_id = 0

    def update(self, faces: list) -> list:
        """
        Update tracked faces with new detections.
        Returns smoothed face positions.
        """
        import time

        # Match new faces to existing tracked faces
        matched = set()
        updated_faces = []

        for face in faces:
            cx, cy, size = face['cx'], face['cy'], face['size']
            best_id = None
            best_dist = float('inf')

            # Find closest tracked face
            for fid, tracked in self._tracked_faces.items():
                if fid in matched:
                    continue
                dist = ((cx - tracked['cx'])**2 + (cy - tracked['cy'])**2)**0.5
                if dist < best_dist and dist < size * 2:  # Within reasonable distance
                    best_dist = dist
                    best_id = fid

            if best_id is not None:
                # Update existing tracked face with smoothing
                matched.add(best_id)
                tracked = self._tracked_faces[best_id]
                tracked['cx'] = tracked['cx'] * self.smoothing + cx * (1 - self.smoothing)
                tracked['cy'] = tracked['cy'] * self.smoothing + cy * (1 - self.smoothing)
                tracked['size'] = tracked['size'] * self.smoothing + size * (1 - self.smoothing)
                tracked['frames_missing'] = 0
                updated_faces.append({
                    'cx': tracked['cx'],
                    'cy': tracked['cy'],
                    'size': tracked['size'],
                    'bbox': face.get('bbox')
                })
            else:
                # New face
                fid = self._next_id
                self._next_id += 1
                self._tracked_faces[fid] = {
                    'cx': cx, 'cy': cy, 'size': size, 'frames_missing': 0
                }
                matched.add(fid)
                updated_faces.append({
                    'cx': cx, 'cy': cy, 'size': size, 'bbox': face.get('bbox')
                })

        # Update missing frames for unmatched faces
        to_remove = []
        for fid, tracked in self._tracked_faces.items():
            if fid not in matched:
                tracked['frames_missing'] += 1
                if tracked['frames_missing'] <= self.persistence_frames:
                    # Keep showing face at last known position
                    updated_faces.append({
                        'cx': tracked['cx'],
                        'cy': tracked['cy'],
                        'size': tracked['size'],
                        'bbox': None
                    })
                else:
                    to_remove.append(fid)

        for fid in to_remove:
            del self._tracked_faces[fid]

        return updated_faces


# Global face tracker instance
_face_tracker = FaceTracker(smoothing=0.6, persistence_frames=10)


def extract_face_crop(
    frame: np.ndarray,
    detections: list,
    keypoints_list: list = None,
    output_size: tuple = (480, 480),
    padding: float = 0.3,
) -> np.ndarray:
    """
    Extract and zoom into faces of ALL detected people.
    Creates a grid if multiple faces. Uses smoothing to prevent flickering.

    Args:
        frame: Original RAW frame (no overlays)
        detections: List of detections
        keypoints_list: Optional pose keypoints for better face localization
        output_size: Size of the output image
        padding: Extra padding around face

    Returns:
        Grid image with all face crops, or None if no person detected
    """
    import cv2

    # Filter for people only
    people = [d for d in detections if d.class_name == "person"]
    if not people:
        # Check if tracker still has persisted faces
        tracked = _face_tracker.update([])
        if not tracked:
            return None
        # Use last known positions
        faces_data = tracked
    else:
        # Extract face data for each person
        faces_data = []
        h, w = frame.shape[:2]

        for person in people:
            x1, y1, x2, y2 = person.bbox
            person_width = x2 - x1
            person_height = y2 - y1

            face_cx, face_cy, face_size = None, None, None

            # Try to find face from pose keypoints
            if keypoints_list:
                for kpts in keypoints_list:
                    if len(kpts) >= 5:
                        nose = kpts[0]
                        left_eye = kpts[1]
                        right_eye = kpts[2]
                        left_ear = kpts[3]
                        right_ear = kpts[4]

                        # Check if nose is within this person's bbox
                        if x1 <= nose[0] <= x2 and y1 <= nose[1] <= y2 and nose[2] > 0.3:
                            valid_points = []
                            for pt in [nose, left_eye, right_eye, left_ear, right_ear]:
                                if pt[2] > 0.3:
                                    valid_points.append((pt[0], pt[1]))

                            if valid_points:
                                face_cx = sum(p[0] for p in valid_points) / len(valid_points)
                                face_cy = sum(p[1] for p in valid_points) / len(valid_points)

                                if left_ear[2] > 0.3 and right_ear[2] > 0.3:
                                    face_size = abs(right_ear[0] - left_ear[0]) * 1.5
                                elif left_eye[2] > 0.3 and right_eye[2] > 0.3:
                                    face_size = abs(right_eye[0] - left_eye[0]) * 3.0
                                break

            # Fallback to bbox estimation
            if face_cx is None:
                face_cx = (x1 + x2) / 2
                face_cy = y1 + person_height * 0.12

            if face_size is None:
                face_size = max(person_width * 0.7, person_height * 0.22)

            faces_data.append({
                'cx': face_cx,
                'cy': face_cy,
                'size': face_size,
                'bbox': person.bbox
            })

        # Update tracker with smoothing
        faces_data = _face_tracker.update(faces_data)

    if not faces_data:
        return None

    h, w = frame.shape[:2]

    # Extract each face crop
    face_crops = []
    for face in faces_data:
        face_cx = face['cx']
        face_cy = face['cy']
        face_size = face['size']

        face_size_padded = face_size * (1 + padding)
        half_size = face_size_padded / 2

        crop_x1 = int(max(0, face_cx - half_size))
        crop_y1 = int(max(0, face_cy - half_size * 0.8))
        crop_x2 = int(min(w, face_cx + half_size))
        crop_y2 = int(min(h, face_cy + half_size * 1.2))

        if crop_x2 - crop_x1 < 30 or crop_y2 - crop_y1 < 30:
            continue

        face_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
        face_crops.append(face_crop)

    if not face_crops:
        return None

    # Create grid layout
    num_faces = len(face_crops)

    if num_faces == 1:
        # Single face - full size
        return cv2.resize(face_crops[0], output_size, interpolation=cv2.INTER_LANCZOS4)

    # Multiple faces - create grid
    # Calculate grid dimensions
    if num_faces == 2:
        cols, rows = 2, 1
    elif num_faces <= 4:
        cols, rows = 2, 2
    elif num_faces <= 6:
        cols, rows = 3, 2
    else:
        cols, rows = 3, 3

    cell_w = output_size[0] // cols
    cell_h = output_size[1] // rows

    # Create output grid
    grid = np.zeros((output_size[1], output_size[0], 3), dtype=np.uint8)

    for i, crop in enumerate(face_crops[:cols * rows]):
        row = i // cols
        col = i % cols

        # Resize crop to fit cell
        resized = cv2.resize(crop, (cell_w, cell_h), interpolation=cv2.INTER_LANCZOS4)

        # Place in grid
        y_start = row * cell_h
        x_start = col * cell_w
        grid[y_start:y_start + cell_h, x_start:x_start + cell_w] = resized

    return grid
