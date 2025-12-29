"""Stream module - video streaming and annotation."""
from .server import StreamServer
from .annotate import annotate_frame

__all__ = [
    "StreamServer",
    "annotate_frame",
]
