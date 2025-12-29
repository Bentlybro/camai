"""Pose estimation using YOLO11-pose."""
import logging
from pathlib import Path
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class PoseEstimator:
    """YOLO pose estimation for skeleton overlay with frame skipping and smoothing."""

    def __init__(self, model_path: str = "yolo11n-pose.engine", confidence: float = 0.5):
        self.model_path = model_path
        self.confidence = confidence
        self._model = None
        self.inference_ms = 0
        self._loaded = False
        # Frame skipping for performance
        self._frame_skip = 2  # Run every N frames (1 = every frame, 2 = every other)
        self._frame_count = 0
        # Smoothing - cache last keypoints
        self._last_keypoints = []
        self._smoothing = 0.4  # Blend factor (0 = no smoothing, 1 = full smoothing)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        """Load the pose model."""
        try:
            from ultralytics import YOLO

            path = Path(self.model_path)

            # Check if we need to export
            if path.suffix == ".engine" and not path.exists():
                pt_path = path.with_suffix(".pt")
                if pt_path.exists():
                    logger.info(f"Exporting pose model to TensorRT: {path}")
                    temp_model = YOLO(str(pt_path))
                    temp_model.export(format="engine", half=True)
                else:
                    logger.warning(f"Pose model not found: {pt_path}")
                    return False

            logger.info(f"Loading pose model: {self.model_path}")
            self._model = YOLO(self.model_path, task="pose")

            # Warmup
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            self._model(dummy, verbose=False)

            logger.info("Pose model loaded")
            self._loaded = True
            return True

        except Exception as e:
            logger.error(f"Failed to load pose model: {e}")
            self._loaded = False
            return False

    def estimate(self, frame: np.ndarray) -> List[List]:
        """
        Run pose estimation on frame with frame skipping and smoothing.

        Returns:
            List of keypoints for each person detected.
            Each keypoint list contains 17 points: [x, y, confidence]
        """
        if self._model is None:
            return self._last_keypoints

        self._frame_count += 1

        # Frame skipping: return cached keypoints on skipped frames
        if self._frame_count % self._frame_skip != 0:
            self.inference_ms = 0  # No inference this frame
            return self._last_keypoints

        import time
        start = time.perf_counter()

        results = self._model(frame, conf=self.confidence, verbose=False)

        self.inference_ms = (time.perf_counter() - start) * 1000

        keypoints_list = []

        for result in results:
            if result.keypoints is None:
                continue

            # result.keypoints.data shape: [num_persons, 17, 3]
            kpts = result.keypoints.data.cpu().numpy()

            for person_kpts in kpts:
                # person_kpts shape: [17, 3] - each row is [x, y, confidence]
                keypoints_list.append(person_kpts.tolist())

        # Apply smoothing if we have previous keypoints
        if self._last_keypoints and keypoints_list and self._smoothing > 0:
            keypoints_list = self._smooth_keypoints(keypoints_list, self._last_keypoints)

        self._last_keypoints = keypoints_list
        return keypoints_list

    def _smooth_keypoints(self, current: List[List], previous: List[List]) -> List[List]:
        """Smooth keypoints between frames for less jitter."""
        smoothed = []
        # Match by count (simple approach - could use position matching for better results)
        for i, curr_person in enumerate(current):
            if i < len(previous):
                prev_person = previous[i]
                smooth_person = []
                for j, (curr_kpt, prev_kpt) in enumerate(zip(curr_person, prev_person)):
                    # Blend current and previous positions
                    cx, cy, cc = curr_kpt
                    px, py, pc = prev_kpt
                    # Only smooth if both have good confidence
                    if cc > 0.3 and pc > 0.3:
                        s = self._smoothing
                        sx = cx * (1 - s) + px * s
                        sy = cy * (1 - s) + py * s
                        smooth_person.append([sx, sy, cc])
                    else:
                        smooth_person.append(curr_kpt)
                smoothed.append(smooth_person)
            else:
                smoothed.append(curr_person)
        return smoothed
