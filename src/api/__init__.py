"""API module - FastAPI web dashboard."""
from .app import app, set_state, get_state, update_stats, add_event

__all__ = ["app", "set_state", "get_state", "update_stats", "add_event"]
