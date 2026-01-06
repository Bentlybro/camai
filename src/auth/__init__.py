"""Authentication module for CAMAI."""
from auth.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_stream_token,
    decode_token,
    is_auth_available,
)
from auth.dependencies import (
    CurrentUser,
    get_current_user,
    get_current_user_optional,
    require_admin,
    get_user_from_stream_token,
    get_user_from_basic_auth,
    require_stream_token,
    get_user_from_ws_token,
)
from auth.routes import router as auth_router
from auth.admin_routes import router as admin_router

__all__ = [
    # Security
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "create_stream_token",
    "decode_token",
    "is_auth_available",
    # Dependencies
    "CurrentUser",
    "get_current_user",
    "get_current_user_optional",
    "require_admin",
    "get_user_from_stream_token",
    "get_user_from_basic_auth",
    "require_stream_token",
    "get_user_from_ws_token",
    # Routers
    "auth_router",
    "admin_router",
]
