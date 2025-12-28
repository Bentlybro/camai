"""Face tracking and extraction for face zoom stream."""
import time
import numpy as np


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


# Global face tracker instance - minimal smoothing for responsive tracking
_face_tracker = FaceTracker(smoothing=0.15, persistence_frames=3)


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

    Simplified approach: Uses nose keypoint from pose estimation if available,
    otherwise estimates head position from person bounding box. This is faster
    and works better at distance than trying to detect facial features.

    Args:
        frame: Original RAW frame (no overlays)
        detections: List of detections
        keypoints_list: Optional pose keypoints for head localization
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

            face_cx, face_cy = None, None

            # Find matching keypoints by checking if nose is inside this person's bbox
            # (keypoints_list is from separate pose detection, not aligned with detections)
            if keypoints_list:
                for kpts in keypoints_list:
                    if len(kpts) >= 3:
                        nose = kpts[0]      # nose
                        left_eye = kpts[1]  # left eye
                        right_eye = kpts[2] # right eye

                        nx, ny, nconf = nose[0], nose[1], nose[2]

                        # Check if nose is inside this person's bbox
                        if nconf > 0.1 and x1 <= nx <= x2 and y1 <= ny <= y2:
                            # Use average of visible head keypoints for better centering
                            points = [(nx, ny)]
                            if left_eye[2] > 0.1:
                                points.append((left_eye[0], left_eye[1]))
                            if right_eye[2] > 0.1:
                                points.append((right_eye[0], right_eye[1]))

                            face_cx = sum(p[0] for p in points) / len(points)
                            face_cy = sum(p[1] for p in points) / len(points)
                            break

            # Fallback to bbox-based estimation (top center of person)
            if face_cx is None:
                face_cx = (x1 + x2) / 2
                face_cy = y1 + person_height * 0.15

            # Head size estimated from person bbox
            face_size = max(person_width * 0.6, person_height * 0.2)

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
