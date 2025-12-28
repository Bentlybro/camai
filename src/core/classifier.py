"""Image classification for detected objects using YOLO11-cls."""
import logging
import cv2
import numpy as np
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Relevant ImageNet classes for our use case
VEHICLE_CLASSES = {
    'sports_car': 'sports car',
    'convertible': 'convertible',
    'cab': 'taxi',
    'jeep': 'jeep',
    'limousine': 'limousine',
    'minivan': 'minivan',
    'pickup': 'pickup truck',
    'racer': 'race car',
    'beach_wagon': 'station wagon',
    'moving_van': 'moving van',
    'police_van': 'police van',
    'trailer_truck': 'semi truck',
    'tow_truck': 'tow truck',
    'garbage_truck': 'garbage truck',
    'fire_engine': 'fire truck',
    'ambulance': 'ambulance',
    'motor_scooter': 'scooter',
    'moped': 'moped',
    'mountain_bike': 'bike',
    'bicycle': 'bicycle',
    'motorcycle': 'motorcycle',
    'school_bus': 'school bus',
    'trolleybus': 'bus',
    'minibus': 'minibus',
}

PERSON_RELATED_CLASSES = {
    'suit': 'wearing suit',
    'lab_coat': 'wearing lab coat',
    'military_uniform': 'in uniform',
    'jean': 'wearing jeans',
    'sweatshirt': 'wearing sweatshirt',
    'jersey': 'wearing jersey',
    'trench_coat': 'wearing coat',
    'fur_coat': 'wearing fur coat',
    'poncho': 'wearing poncho',
    'cardigan': 'wearing cardigan',
    'backpack': 'with backpack',
    'briefcase': 'with briefcase',
    'umbrella': 'with umbrella',
    'sunglasses': 'wearing sunglasses',
    'cowboy_hat': 'wearing cowboy hat',
    'sombrero': 'wearing sombrero',
    'baseball': 'with baseball',
    'basketball': 'with basketball',
    'soccer_ball': 'with soccer ball',
}

# Common color names for color extraction
COLOR_NAMES = {
    'black': (0, 0, 0),
    'white': (255, 255, 255),
    'gray': (128, 128, 128),
    'silver': (192, 192, 192),
    'red': (255, 0, 0),
    'blue': (0, 0, 255),
    'green': (0, 128, 0),
    'yellow': (255, 255, 0),
    'orange': (255, 165, 0),
    'brown': (139, 69, 19),
    'beige': (245, 245, 220),
    'navy': (0, 0, 128),
    'maroon': (128, 0, 0),
    'purple': (128, 0, 128),
    'teal': (0, 128, 128),
}


@dataclass
class ClassificationResult:
    """Result from image classification."""
    top_class: str
    confidence: float
    top_5: List[Tuple[str, float]]
    color: Optional[str] = None
    description: str = ""


class ImageClassifier:
    """Classifies cropped detection regions using YOLO11-cls."""

    def __init__(self, model_path: str = "yolo11n-cls.engine", confidence: float = 0.3):
        self.model_path = model_path
        self.confidence = confidence
        self._model = None
        self._loaded = False
        self.inference_ms = 0.0

    def load(self) -> bool:
        """Load the classification model."""
        try:
            from ultralytics import YOLO

            model_file = Path(self.model_path)

            # Check for TensorRT engine first, then PT file
            if model_file.exists():
                logger.info(f"Loading classifier: {self.model_path}")
                self._model = YOLO(str(model_file), task='classify')
            elif model_file.with_suffix('.pt').exists():
                pt_path = model_file.with_suffix('.pt')
                logger.info(f"Loading classifier: {pt_path}")
                self._model = YOLO(str(pt_path), task='classify')
            else:
                # Download the model
                logger.info("Downloading yolo11n-cls model...")
                self._model = YOLO('yolo11n-cls.pt', task='classify')

                # Export to TensorRT if on Jetson
                try:
                    logger.info("Exporting classifier to TensorRT...")
                    self._model.export(format='engine', imgsz=224, half=True)
                    # Reload the engine
                    engine_path = Path('yolo11n-cls.engine')
                    if engine_path.exists():
                        self._model = YOLO(str(engine_path), task='classify')
                        logger.info("Classifier TensorRT engine loaded")
                except Exception as e:
                    logger.warning(f"TensorRT export failed, using PyTorch: {e}")

            self._loaded = True
            logger.info("Classifier loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load classifier: {e}")
            return False

    def classify(self, frame: np.ndarray, bbox: Tuple[int, int, int, int],
                 class_name: str = None) -> Optional[ClassificationResult]:
        """
        Classify a cropped region of the frame.

        Args:
            frame: Full frame image
            bbox: Bounding box (x1, y1, x2, y2)
            class_name: Detection class (person, car, truck) for context

        Returns:
            ClassificationResult with top classes and color
        """
        if not self._loaded:
            return None

        try:
            import time
            start = time.time()

            # Crop the region with some padding
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [int(c) for c in bbox]

            # Add 10% padding
            pad_x = int((x2 - x1) * 0.1)
            pad_y = int((y2 - y1) * 0.1)
            x1 = max(0, x1 - pad_x)
            y1 = max(0, y1 - pad_y)
            x2 = min(w, x2 + pad_x)
            y2 = min(h, y2 + pad_y)

            crop = frame[y1:y2, x1:x2]

            if crop.size == 0:
                return None

            # Run classification
            results = self._model(crop, verbose=False)

            self.inference_ms = (time.time() - start) * 1000

            if not results or len(results) == 0:
                return None

            result = results[0]
            probs = result.probs

            if probs is None:
                return None

            # Get top 5 predictions
            top5_indices = probs.top5
            top5_conf = probs.top5conf.cpu().numpy()
            names = result.names

            top_5 = [(names[idx], float(conf)) for idx, conf in zip(top5_indices, top5_conf)]
            top_class = top_5[0][0] if top_5 else "unknown"
            top_conf = top_5[0][1] if top_5 else 0.0

            # Extract dominant color
            color = self._extract_color(crop, class_name)

            # Build description based on context
            description = self._build_description(top_5, color, class_name)

            return ClassificationResult(
                top_class=top_class,
                confidence=top_conf,
                top_5=top_5,
                color=color,
                description=description
            )

        except Exception as e:
            logger.error(f"Classification error: {e}")
            return None

    def _extract_color(self, crop: np.ndarray, class_name: str = None) -> str:
        """Extract dominant color from cropped region."""
        try:
            # For vehicles, focus on the main body (center region)
            h, w = crop.shape[:2]
            if class_name in ('car', 'truck'):
                # Focus on center 60% to avoid windows/wheels
                y1, y2 = int(h * 0.2), int(h * 0.8)
                x1, x2 = int(w * 0.2), int(w * 0.8)
                region = crop[y1:y2, x1:x2]
            else:
                region = crop

            if region.size == 0:
                region = crop

            # Convert to RGB and get average color
            rgb = cv2.cvtColor(region, cv2.COLOR_BGR2RGB)

            # Use K-means to find dominant colors
            pixels = rgb.reshape(-1, 3).astype(np.float32)

            # Simple: just use median color (faster than k-means)
            median_color = np.median(pixels, axis=0).astype(int)

            # Find closest named color
            min_dist = float('inf')
            closest_color = 'unknown'

            for name, rgb_val in COLOR_NAMES.items():
                dist = np.sqrt(np.sum((median_color - np.array(rgb_val)) ** 2))
                if dist < min_dist:
                    min_dist = dist
                    closest_color = name

            return closest_color

        except Exception as e:
            logger.debug(f"Color extraction error: {e}")
            return "unknown"

    def _build_description(self, top_5: List[Tuple[str, float]], color: str,
                          class_name: str = None) -> str:
        """Build a human-readable description from classification results."""
        parts = []

        # Add color for vehicles
        if class_name in ('car', 'truck') and color and color != 'unknown':
            parts.append(color)

        # Check for relevant vehicle classes in top predictions
        if class_name in ('car', 'truck'):
            for cls, conf in top_5[:3]:
                cls_lower = cls.lower().replace(' ', '_')
                if cls_lower in VEHICLE_CLASSES and conf > 0.15:
                    parts.append(VEHICLE_CLASSES[cls_lower])
                    break
            else:
                parts.append(class_name)

        # Check for person-related classes
        elif class_name == 'person':
            for cls, conf in top_5[:3]:
                cls_lower = cls.lower().replace(' ', '_')
                if cls_lower in PERSON_RELATED_CLASSES and conf > 0.15:
                    parts.append(f"person {PERSON_RELATED_CLASSES[cls_lower]}")
                    break
            else:
                if color and color != 'unknown':
                    parts.append(f"person in {color}")
                else:
                    parts.append("person")
        else:
            parts.append(class_name or "object")

        return " ".join(parts)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
