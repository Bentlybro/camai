"""Face detection for person crops."""
import logging
import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class FaceDetector:
    """Detect faces in images using OpenCV DNN or Haar cascades."""

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence
        self._net = None
        self._cascade = None
        self._method = None  # 'dnn' or 'haar'
        self._loaded = False

    def load(self) -> bool:
        """Load face detection model."""
        # Try DNN first (more accurate)
        if self._load_dnn():
            self._method = 'dnn'
            self._loaded = True
            logger.info("Face detector loaded (DNN method)")
            return True

        # Fallback to Haar cascades (always available with OpenCV)
        if self._load_haar():
            self._method = 'haar'
            self._loaded = True
            logger.info("Face detector loaded (Haar cascade method)")
            return True

        logger.warning("Face detector failed to load")
        return False

    def _load_dnn(self) -> bool:
        """Load OpenCV DNN face detector."""
        try:
            # Check for pre-downloaded model files
            model_dir = Path(__file__).parent.parent / "models"
            prototxt = model_dir / "deploy.prototxt"
            caffemodel = model_dir / "res10_300x300_ssd_iter_140000.caffemodel"

            if prototxt.exists() and caffemodel.exists():
                self._net = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
                return True

            # Try to download the model
            logger.info("Downloading face detection model...")
            model_dir.mkdir(parents=True, exist_ok=True)

            import urllib.request

            # OpenCV face detection model URLs
            prototxt_url = "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt"
            caffemodel_url = "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel"

            urllib.request.urlretrieve(prototxt_url, str(prototxt))
            urllib.request.urlretrieve(caffemodel_url, str(caffemodel))

            self._net = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
            logger.info("Face detection model downloaded successfully")
            return True

        except Exception as e:
            logger.debug(f"DNN face detector load failed: {e}")
            return False

    def _load_haar(self) -> bool:
        """Load Haar cascade face detector."""
        try:
            # OpenCV includes this cascade
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self._cascade = cv2.CascadeClassifier(cascade_path)
            if self._cascade.empty():
                return False
            return True
        except Exception as e:
            logger.debug(f"Haar cascade load failed: {e}")
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def detect(self, image: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
        """
        Detect faces in image.

        Returns:
            List of (x1, y1, x2, y2, confidence) tuples
        """
        if not self._loaded:
            return []

        if self._method == 'dnn':
            return self._detect_dnn(image)
        else:
            return self._detect_haar(image)

    def _detect_dnn(self, image: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
        """Detect faces using DNN."""
        h, w = image.shape[:2]

        # Create blob and run inference
        blob = cv2.dnn.blobFromImage(
            cv2.resize(image, (300, 300)), 1.0, (300, 300),
            (104.0, 177.0, 123.0), swapRB=False, crop=False
        )
        self._net.setInput(blob)
        detections = self._net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence > self.min_confidence:
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                x1, y1, x2, y2 = box.astype(int)
                # Clamp to image bounds
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 > x1 and y2 > y1:
                    faces.append((x1, y1, x2, y2, float(confidence)))

        return faces

    def _detect_haar(self, image: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
        """Detect faces using Haar cascade."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detections = self._cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
        )

        faces = []
        for (x, y, w, h) in detections:
            # Haar doesn't provide confidence, use 0.8 as default
            faces.append((x, y, x + w, y + h, 0.8))

        return faces

    def detect_largest(self, image: np.ndarray) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Detect the largest face in image.

        Returns:
            (x1, y1, x2, y2, confidence) or None if no face found
        """
        faces = self.detect(image)
        if not faces:
            return None

        # Return largest by area
        return max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))

    def crop_face(self, image: np.ndarray, padding: float = 0.3) -> Optional[np.ndarray]:
        """
        Detect and crop the largest face with padding.

        Args:
            image: Input image
            padding: Extra padding around face (0.3 = 30% extra on each side)

        Returns:
            Cropped face image or None if no face found
        """
        face = self.detect_largest(image)
        if face is None:
            return None

        x1, y1, x2, y2, conf = face
        h, w = image.shape[:2]

        # Add padding
        face_w = x2 - x1
        face_h = y2 - y1
        pad_x = int(face_w * padding)
        pad_y = int(face_h * padding)

        # Expand box with padding, clamped to image bounds
        x1 = max(0, x1 - pad_x)
        y1 = max(0, y1 - pad_y)
        x2 = min(w, x2 + pad_x)
        y2 = min(h, y2 + pad_y)

        return image[y1:y2, x1:x2].copy()


# Singleton instance
_face_detector = None


def get_face_detector() -> FaceDetector:
    """Get the face detector singleton."""
    global _face_detector
    if _face_detector is None:
        _face_detector = FaceDetector()
        _face_detector.load()
    return _face_detector
