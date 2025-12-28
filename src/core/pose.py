"""Pose estimation using YOLO11-pose."""
import logging
from pathlib import Path
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class PoseEstimator:
    """YOLO pose estimation for skeleton overlay."""

    def __init__(self, model_path: str = "yolo11n-pose.engine", confidence: float = 0.5):
        self.model_path = model_path
        self.confidence = confidence
        self._model = None
        self.inference_ms = 0
        self._loaded = False

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
        Run pose estimation on frame.

        Returns:
            List of keypoints for each person detected.
            Each keypoint list contains 17 points: [x, y, confidence]
        """
        if self._model is None:
            return []

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

        return keypoints_list
