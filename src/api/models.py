"""Pydantic models for API requests/responses."""
from pydantic import BaseModel


class DetectionSettings(BaseModel):
    confidence: float = 0.5
    iou_threshold: float = 0.45


class PTZSettings(BaseModel):
    enabled: bool = False
    track_speed: float = 0.5
    deadzone: float = 0.15


class PTZConnectionSettings(BaseModel):
    host: str = ""
    port: int = 2020
    username: str = ""
    password: str = ""


class PTZMoveRequest(BaseModel):
    pan: float = 0   # -1.0 (left) to 1.0 (right)
    tilt: float = 0  # -1.0 (down) to 1.0 (up)


class PoseSettings(BaseModel):
    enabled: bool = False


class ClassifierSettings(BaseModel):
    enabled: bool = True


class ModelsStatus(BaseModel):
    """Status of all models - for runtime toggling."""
    pose_enabled: bool = False
    classifier_enabled: bool = True


class DisplaySettings(BaseModel):
    show_overlays: bool = True
    detect_person: bool = True
    detect_vehicle: bool = True
    detect_package: bool = True


class StreamSettings(BaseModel):
    quality: int = 70
    width: int = 640
    height: int = 480
