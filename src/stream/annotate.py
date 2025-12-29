"""Frame annotation - draw bounding boxes, skeletons, and stats."""
import cv2
import numpy as np

# Body skeleton connections (no face - indices 5-16 only)
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

# Pre-defined colors (avoid dict lookup in hot path)
COLOR_PERSON = (0, 255, 0)
COLOR_CAR = (255, 0, 0)
COLOR_TRUCK = (255, 128, 0)
COLOR_PACKAGE = (0, 255, 255)
COLOR_DEFAULT = (128, 128, 128)
COLOR_SKELETON = (0, 255, 255)
COLOR_JOINT = (0, 165, 255)

# Font settings
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _get_color(class_name: str) -> tuple:
    """Get color for class (optimized)."""
    if class_name == "person":
        return COLOR_PERSON
    elif class_name == "car":
        return COLOR_CAR
    elif class_name == "truck":
        return COLOR_TRUCK
    elif class_name == "package":
        return COLOR_PACKAGE
    return COLOR_DEFAULT


def annotate_frame(
    frame: np.ndarray,
    detections: list,
    fps: float = 0,
    inference_ms: float = 0,
    keypoints_list: list = None,
) -> np.ndarray:
    """Draw boxes, skeletons, and stats on frame (modifies in place for speed)."""
    h, w = frame.shape[:2]

    # Draw detections
    for d in detections:
        x1, y1, x2, y2 = d.bbox
        color = _get_color(d.class_name)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        # Use description if available
        label = f"{d.description.upper() if d.description else d.class_name.upper()} {d.confidence:.0%}"
        cv2.putText(frame, label, (x1, y1 - 5), FONT, 0.5, color, 2)

    # Draw pose skeletons if available
    if keypoints_list:
        _draw_skeletons_fast(frame, keypoints_list)

    # Stats overlay (top right) - pre-format strings
    stats_text = f"FPS: {fps:.1f} | Inf: {inference_ms:.1f}ms"
    cv2.putText(frame, stats_text, (w - 200, 25), FONT, 0.6, COLOR_PERSON, 2)

    return frame


def _draw_skeletons_fast(frame: np.ndarray, keypoints_list: list):
    """Draw body skeletons on frame (optimized, no face keypoints)."""
    for keypoints in keypoints_list:
        if keypoints is None or len(keypoints) < 17:
            continue

        # Draw skeleton lines (body only)
        for start_idx, end_idx in BODY_SKELETON:
            x1, y1, conf1 = keypoints[start_idx]
            x2, y2, conf2 = keypoints[end_idx]

            if conf1 > 0.5 and conf2 > 0.5:
                cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), COLOR_SKELETON, 2)

        # Draw joints (body only - indices 5-16)
        for i in range(5, 17):
            x, y, conf = keypoints[i]
            if conf > 0.5:
                cv2.circle(frame, (int(x), int(y)), 4, COLOR_JOINT, -1)
