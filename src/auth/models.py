"""Pydantic models for authentication."""
from typing import Optional
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """User registration request."""
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    """User login request."""
    username: str
    password: str


class TokenResponse(BaseModel):
    """Token response after login."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: "UserResponse"


class RefreshRequest(BaseModel):
    """Token refresh request."""
    refresh_token: str


class StreamTokenResponse(BaseModel):
    """Stream token response."""
    stream_token: str
    expires_in: int  # seconds


class UserResponse(BaseModel):
    """User data response (no sensitive fields)."""
    id: int
    username: str
    role: str
    approved: bool
    created_at: Optional[str] = None
    last_login: Optional[str] = None


class UserListResponse(BaseModel):
    """List of users response."""
    users: list[UserResponse]
    total: int


class ChangePasswordRequest(BaseModel):
    """Password change request."""
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=100)


class RoleChangeRequest(BaseModel):
    """Role change request."""
    role: str = Field(..., pattern="^(admin|user)$")


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
    success: bool = True


# Update forward references
TokenResponse.model_rebuild()
