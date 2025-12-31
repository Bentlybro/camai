"""Authentication API routes."""
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, status, Depends

from database import get_database
from auth.models import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    StreamTokenResponse,
    UserResponse,
    ChangePasswordRequest,
    MessageResponse,
)
from auth.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    create_stream_token,
    decode_token,
    is_auth_available,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    STREAM_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from auth.dependencies import get_current_user, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.post("/register", response_model=MessageResponse)
async def register(request: RegisterRequest):
    """
    Register a new user.
    First user automatically becomes admin and is approved.
    Subsequent users require admin approval.
    """
    if not is_auth_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not available. Install: pip install passlib[bcrypt] python-jose[cryptography]",
        )

    db = get_database()

    # Check if username already exists
    existing = db.get_user_by_username(request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken",
        )

    # First user is admin and auto-approved
    is_first_user = db.get_user_count() == 0

    user = db.create_user(
        username=request.username,
        password_hash=hash_password(request.password),
        role="admin" if is_first_user else "user",
        approved=1 if is_first_user else 0,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )

    if is_first_user:
        logger.info(f"First user registered as admin: {request.username}")
        return MessageResponse(
            message="Admin account created. You can now log in.",
            success=True,
        )
    else:
        logger.info(f"New user registered (pending approval): {request.username}")
        return MessageResponse(
            message="Registration submitted. Please wait for admin approval.",
            success=True,
        )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login with username and password.
    Returns access and refresh tokens.
    """
    if not is_auth_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not available",
        )

    db = get_database()
    user = db.get_user_by_username(request.username)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user["approved"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval. Please wait for an admin to approve your account.",
        )

    # Create tokens
    access_token = create_access_token(user["id"], user["username"], user["role"])
    refresh_token = create_refresh_token(user["id"])

    # Store session in database
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    db.create_session(user["id"], refresh_token, expires_at)

    # Update last login
    db.update_user_last_login(user["id"])

    logger.info(f"User logged in: {user['username']}")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            role=user["role"],
            approved=bool(user["approved"]),
            created_at=user.get("created_at"),
            last_login=user.get("last_login"),
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshRequest):
    """
    Refresh access token using refresh token.
    """
    if not is_auth_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not available",
        )

    # Decode refresh token
    payload = decode_token(request.refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    db = get_database()

    # Check if session exists and is valid
    session = db.get_session_by_token(request.refresh_token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    # Get user
    user = db.get_user_by_id(session["user_id"])
    if not user or not user["approved"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not approved",
        )

    # Create new access token
    access_token = create_access_token(user["id"], user["username"], user["role"])

    return TokenResponse(
        access_token=access_token,
        refresh_token=request.refresh_token,  # Keep same refresh token
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(
            id=user["id"],
            username=user["username"],
            role=user["role"],
            approved=bool(user["approved"]),
            created_at=user.get("created_at"),
            last_login=user.get("last_login"),
        ),
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    user: CurrentUser = Depends(get_current_user),
):
    """
    Logout current user (invalidate all sessions).
    """
    db = get_database()
    deleted = db.delete_user_sessions(user.id)

    logger.info(f"User logged out: {user.username} ({deleted} sessions invalidated)")

    return MessageResponse(message="Logged out successfully")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user: CurrentUser = Depends(get_current_user),
):
    """Get current user information."""
    db = get_database()
    user_data = db.get_user_by_id(user.id)

    return UserResponse(
        id=user_data["id"],
        username=user_data["username"],
        role=user_data["role"],
        approved=bool(user_data["approved"]),
        created_at=user_data.get("created_at"),
        last_login=user_data.get("last_login"),
    )


@router.put("/me/password", response_model=MessageResponse)
async def change_password(
    request: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Change current user's password."""
    db = get_database()
    user_data = db.get_user_by_id(user.id)

    if not verify_password(request.current_password, user_data["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    db.update_user_password(user.id, hash_password(request.new_password))

    # Invalidate all sessions (force re-login)
    db.delete_user_sessions(user.id)

    logger.info(f"Password changed for user: {user.username}")

    return MessageResponse(message="Password changed. Please log in again.")


@router.get("/stream-token", response_model=StreamTokenResponse)
async def get_stream_token(
    user: CurrentUser = Depends(get_current_user),
):
    """
    Get a short-lived token for stream authentication.
    Used for MJPEG streams which can't use Authorization headers.
    """
    db = get_database()

    # Find user's active session
    # For simplicity, create a new stream token for any request
    stream_token = create_stream_token()
    expires_at = datetime.utcnow() + timedelta(minutes=STREAM_TOKEN_EXPIRE_MINUTES)

    # Store in a session or create one
    # Find any existing session for this user
    sessions = db.get_session_by_token
    # For now, create session with stream token directly
    session_id = db.create_session(user.id, create_refresh_token(user.id), expires_at + timedelta(days=1))
    db.update_stream_token(session_id, stream_token, expires_at)

    return StreamTokenResponse(
        stream_token=stream_token,
        expires_in=STREAM_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/status")
async def auth_status():
    """Check authentication system status."""
    db = get_database()
    user_count = db.get_user_count()

    return {
        "auth_available": is_auth_available(),
        "user_count": user_count,
        "setup_required": user_count == 0,
    }
