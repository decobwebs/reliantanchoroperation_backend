from functools import wraps
from fastapi import Depends, HTTPException, status
from app.dependencies import get_current_user
from app.models.user import User
from app.models.enums import UserRole


def require_roles(*roles: UserRole):
    """
    Decorator-style RBAC for route handlers.
    Injects current_user and validates role membership.

    Usage:
        @router.get("/some-endpoint")
        @require_roles(UserRole.bunker_manager, UserRole.ops_supervisor)
        async def my_endpoint(current_user: User = Depends(get_current_user)):
            ...

    Note: FastAPI dependency injection via Depends() is the preferred approach.
    This decorator is kept for consistency and backwards-compatible helper usage.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: User = Depends(get_current_user), **kwargs):
            if current_user.role not in roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions. Required roles: {[r.value for r in roles]}",
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator
