"""Frame annotation - draw bounding boxes, skeletons, and stats."""
import numpy as np
from datetime import datetime


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

        # Use description if available (includes color + type), otherwise fallback to class
        if d.description:
            label = f"{d.description.upper()} {d.confidence:.0%}"
        else:
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
