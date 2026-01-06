"""FastAPI dependencies for authentication."""
import logging
from typing import Optional
from fastapi import Depends, HTTPException, status, Query, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials

from database import get_database
from auth.security import decode_token, verify_password

logger = logging.getLogger(__name__)

# HTTP Bearer token extractor
security = HTTPBearer(auto_error=False)

# HTTP Basic auth extractor (for stream endpoints)
basic_security = HTTPBasic(auto_error=False)


class CurrentUser:
    """Represents the current authenticated user."""
    def __init__(self, user_id: int, username: str, role: str, approved: bool, session_id: int = None):
        self.id = user_id
        self.username = username
        self.role = role
        self.approved = approved
        self.session_id = session_id

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> CurrentUser:
    """
    Get current authenticated user from JWT token.
    Raises 401 if not authenticated or token invalid.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check token type
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database to ensure they still exist and are approved
    db = get_database()
    user_id = int(payload.get("sub"))
    user = db.get_user_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("approved"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval",
        )

    return CurrentUser(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        approved=bool(user["approved"]),
    )


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[CurrentUser]:
    """
    Get current user if authenticated, None otherwise.
    Does not raise exceptions for missing/invalid tokens.
    """
    if not credentials:
        return None

    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


async def require_admin(
    user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """Require admin role. Raises 403 if not admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_user_from_stream_token(
    token: str = Query(None, description="Stream authentication token")
) -> Optional[CurrentUser]:
    """
    Get user from stream token (used for MJPEG streams).
    Returns None if token is missing/invalid.
    """
    if not token:
        return None

    db = get_database()
    session = db.get_session_by_stream_token(token)

    if not session:
        return None

    if not session.get("approved"):
        return None

    return CurrentUser(
        user_id=session["user_id"],
        username=session["username"],
        role=session["role"],
        approved=bool(session["approved"]),
        session_id=session["id"],
    )


async def get_user_from_basic_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(basic_security)
) -> Optional[CurrentUser]:
    """
    Get user from HTTP Basic Auth credentials.
    Used for MJPEG streams with Home Assistant and similar integrations.
    """
    if not credentials:
        return None

    db = get_database()
    user = db.get_user_by_username(credentials.username)

    if not user:
        return None

    if not verify_password(credentials.password, user["password_hash"]):
        return None

    if not user.get("approved"):
        return None

    return CurrentUser(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        approved=bool(user["approved"]),
    )


async def require_stream_token(
    token_user: Optional[CurrentUser] = Depends(get_user_from_stream_token),
    basic_user: Optional[CurrentUser] = Depends(get_user_from_basic_auth)
) -> CurrentUser:
    """
    Require valid stream authentication.
    Accepts either stream token (?token=xxx) or HTTP Basic Auth.
    """
    user = token_user or basic_user
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid stream token or basic auth required",
            headers={"WWW-Authenticate": "Basic realm=\"Camera Stream\""},
        )
    return user


async def get_user_from_ws_token(token: str) -> Optional[CurrentUser]:
    """
    Validate token for WebSocket connections.
    Can accept either access token or stream token.
    """
    if not token:
        return None

    # Try as access token first
    payload = decode_token(token)
    if payload and payload.get("type") == "access":
        db = get_database()
        user_id = int(payload.get("sub"))
        user = db.get_user_by_id(user_id)

        if user and user.get("approved"):
            return CurrentUser(
                user_id=user["id"],
                username=user["username"],
                role=user["role"],
                approved=bool(user["approved"]),
            )

    # Try as stream token
    db = get_database()
    session = db.get_session_by_stream_token(token)

    if session and session.get("approved"):
        return CurrentUser(
            user_id=session["user_id"],
            username=session["username"],
            role=session["role"],
            approved=bool(session["approved"]),
            session_id=session["id"],
        )

    return None
