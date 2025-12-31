"""Security utilities for authentication."""
import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Try to import security libraries
try:
    from passlib.context import CryptContext
    PASSLIB_AVAILABLE = True
except ImportError:
    PASSLIB_AVAILABLE = False
    logger.warning("passlib not installed. Run: pip install passlib[bcrypt]")

try:
    from jose import jwt, JWTError
    JOSE_AVAILABLE = True
except ImportError:
    JOSE_AVAILABLE = False
    logger.warning("python-jose not installed. Run: pip install python-jose[cryptography]")

# Configuration
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30
STREAM_TOKEN_EXPIRE_MINUTES = 5

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if PASSLIB_AVAILABLE else None


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    if not PASSLIB_AVAILABLE:
        raise RuntimeError("passlib not installed")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    if not PASSLIB_AVAILABLE:
        raise RuntimeError("passlib not installed")
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int, username: str, role: str) -> str:
    """Create a JWT access token."""
    if not JOSE_AVAILABLE:
        raise RuntimeError("python-jose not installed")

    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """Create a JWT refresh token."""
    if not JOSE_AVAILABLE:
        raise RuntimeError("python-jose not installed")

    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_stream_token() -> str:
    """Create a short-lived token for stream authentication."""
    return secrets.token_urlsafe(32)


def decode_token(token: str) -> Optional[Dict]:
    """Decode and validate a JWT token. Returns payload or None if invalid."""
    if not JOSE_AVAILABLE:
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        logger.debug(f"Token decode error: {e}")
        return None


def get_token_expiry(token_type: str = "access") -> datetime:
    """Get expiry datetime for a token type."""
    if token_type == "refresh":
        return datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    elif token_type == "stream":
        return datetime.utcnow() + timedelta(minutes=STREAM_TOKEN_EXPIRE_MINUTES)
    else:
        return datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)


def is_auth_available() -> bool:
    """Check if authentication dependencies are available."""
    return PASSLIB_AVAILABLE and JOSE_AVAILABLE
