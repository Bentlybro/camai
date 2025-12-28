"""Stream module - backwards compatibility wrapper.

This module re-exports from the new modular structure in stream/.
"""
from stream.server import StreamServer
from stream.annotate import annotate_frame

__all__ = [
    "StreamServer",
    "annotate_frame",
]
