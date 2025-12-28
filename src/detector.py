"""YOLO detection with TensorRT acceleration."""
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """Single detection result."""
    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    center: Tuple[int, int]
    area: int


class YOLODetector:
    """YOLO detector with automatic TensorRT export."""

    def __init__(
        self,
        model_path: str = "yolo11n.engine",
        conf: float = 0.5,
        iou: float = 0.45,
        classes: List[int] = None,
        class_names: dict = None,
    ):
        self.model_path = model_path
        self.conf = conf
        self.iou = iou
        self.classes = classes or [0, 2, 7, 24, 26, 28]
        self.class_names = class_names or {
            0: "person", 2: "car", 7: "truck",
            24: "package", 26: "package", 28: "package"
        }
        self.model = None
        self._times = []

    def load(self):
        """Load or export model to TensorRT."""
        from ultralytics import YOLO

        path = Path(self.model_path)

        # If .engine exists, load directly
        if path.suffix == ".engine" and path.exists():
            logger.info(f"Loading TensorRT: {path}")
            self.model = YOLO(str(path))
            return

        # Check for .engine version of .pt file
        if path.suffix == ".pt":
            engine = path.with_suffix(".engine")
            if engine.exists():
                logger.info(f"Loading TensorRT: {engine}")
                self.model = YOLO(str(engine))
                return

        # Need to export - download model if needed
        model_name = path.stem if path.suffix else "yolo11n"
        logger.info(f"Loading {model_name}.pt and exporting to TensorRT INT8...")

        self.model = YOLO(f"{model_name}.pt")
        engine = self.model.export(format="engine", int8=True, data="coco128.yaml")
        logger.info(f"Exported: {engine}")
        self.model = YOLO(engine)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """Run detection on frame."""
        start = time.perf_counter()

        results = self.model(
            frame,
            conf=self.conf,
            iou=self.iou,
            classes=self.classes,
            verbose=False,
        )

        # Track timing
        ms = (time.perf_counter() - start) * 1000
        self._times.append(ms)
        if len(self._times) > 100:
            self._times.pop(0)

        # Parse results
        detections = []
        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                cls = int(r.boxes.cls[i])
                conf = float(r.boxes.conf[i])
                x1, y1, x2, y2 = r.boxes.xyxy[i].cpu().numpy().astype(int)

                detections.append(Detection(
                    class_id=cls,
                    class_name=self.class_names.get(cls, "unknown"),
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    center=((x1+x2)//2, (y1+y2)//2),
                    area=(x2-x1) * (y2-y1),
                ))

        return detections

    @property
    def inference_ms(self) -> float:
        return sum(self._times) / len(self._times) if self._times else 0

    @property
    def inference_fps(self) -> float:
        ms = self.inference_ms
        return 1000 / ms if ms > 0 else 0
