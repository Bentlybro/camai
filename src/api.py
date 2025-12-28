"""API module - backwards compatibility wrapper.

This module re-exports from the new modular structure in api/.
"""
from api.app import app, set_state, get_state, update_stats, add_event

__all__ = [
    "app",
    "set_state",
    "get_state",
    "update_stats",
    "add_event",
]
