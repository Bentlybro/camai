"""Admin user management routes."""
import logging
from fastapi import APIRouter, HTTPException, status, Depends

from database import get_database
from auth.models import (
    UserResponse,
    UserListResponse,
    RoleChangeRequest,
    MessageResponse,
)
from auth.dependencies import require_admin, CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


@router.get("", response_model=UserListResponse)
async def list_users(
    admin: CurrentUser = Depends(require_admin),
):
    """List all users (admin only)."""
    db = get_database()
    users = db.get_all_users()

    return UserListResponse(
        users=[
            UserResponse(
                id=u["id"],
                username=u["username"],
                role=u["role"],
                approved=bool(u["approved"]),
                created_at=u.get("created_at"),
                last_login=u.get("last_login"),
            )
            for u in users
        ],
        total=len(users),
    )


@router.get("/pending", response_model=UserListResponse)
async def list_pending_users(
    admin: CurrentUser = Depends(require_admin),
):
    """List users pending approval (admin only)."""
    db = get_database()
    users = db.get_pending_users()

    return UserListResponse(
        users=[
            UserResponse(
                id=u["id"],
                username=u["username"],
                role=u["role"],
                approved=False,
                created_at=u.get("created_at"),
            )
            for u in users
        ],
        total=len(users),
    )


@router.post("/{user_id}/approve", response_model=MessageResponse)
async def approve_user(
    user_id: int,
    admin: CurrentUser = Depends(require_admin),
):
    """Approve a pending user (admin only)."""
    db = get_database()

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user["approved"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already approved",
        )

    db.approve_user(user_id)
    logger.info(f"User approved by {admin.username}: {user['username']}")

    return MessageResponse(message=f"User '{user['username']}' has been approved")


@router.put("/{user_id}/role", response_model=MessageResponse)
async def change_user_role(
    user_id: int,
    request: RoleChangeRequest,
    admin: CurrentUser = Depends(require_admin),
):
    """Change a user's role (admin only)."""
    db = get_database()

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent demoting yourself
    if user_id == admin.id and request.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot demote yourself",
        )

    # Ensure at least one admin remains
    if user["role"] == "admin" and request.role == "user":
        all_users = db.get_all_users()
        admin_count = sum(1 for u in all_users if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last admin",
            )

    db.update_user_role(user_id, request.role)
    logger.info(f"User role changed by {admin.username}: {user['username']} -> {request.role}")

    return MessageResponse(message=f"User '{user['username']}' role changed to '{request.role}'")


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: int,
    admin: CurrentUser = Depends(require_admin),
):
    """Delete/reject a user (admin only)."""
    db = get_database()

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent deleting yourself
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    # Ensure at least one admin remains
    if user["role"] == "admin":
        all_users = db.get_all_users()
        admin_count = sum(1 for u in all_users if u["role"] == "admin")
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last admin",
            )

    db.delete_user(user_id)
    logger.info(f"User deleted by {admin.username}: {user['username']}")

    return MessageResponse(message=f"User '{user['username']}' has been deleted")


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    admin: CurrentUser = Depends(require_admin),
):
    """Get a specific user (admin only)."""
    db = get_database()

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserResponse(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        approved=bool(user["approved"]),
        created_at=user.get("created_at"),
        last_login=user.get("last_login"),
    )
