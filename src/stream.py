"""Stream module - backwards compatibility wrapper.

This module re-exports from the new modular structure in stream/.
"""
from stream.server import StreamServer
from stream.annotate import annotate_frame, draw_skeletons, BODY_SKELETON
from stream.face import FaceTracker, extract_face_crop

__all__ = [
    "StreamServer",
    "annotate_frame",
    "draw_skeletons",
    "BODY_SKELETON",
    "FaceTracker",
    "extract_face_crop",
]
