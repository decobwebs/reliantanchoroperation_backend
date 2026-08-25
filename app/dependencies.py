from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.enums import UserRole
from app.permissions import OPERATION_MANAGER_ROLES
from app.services.auth_service import AuthService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate Bearer JWT, return authenticated User."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await AuthService.get_user_from_token(credentials.credentials, db)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Like get_current_user but returns None if no credentials."""
    if not credentials:
        return None
    try:
        return await AuthService.get_user_from_token(credentials.credentials, db)
    except HTTPException:
        return None


def get_effective_role(current_user: User = Depends(get_current_user)) -> UserRole:
    """The role permission checks should gate on: the BM's acting-as role if
    set, otherwise their real role. `current_user.role`/`.id` are never
    mutated — only this comparison value is redirected."""
    return current_user.acting_as_role or current_user.role


def require_roles(*roles: UserRole, allow_acting_as: bool = True):
    """
    FastAPI dependency factory that enforces role-based access control.
    Usage: Depends(require_roles(UserRole.bunker_manager))

    A real Bunker Manager always passes: the BM has full access to every
    role's actions at all times, whether or not they are "acting as" someone
    else and whether or not the underlying task/activity is assigned to them.
    "Acting as" is a preview of another role's view, never a downgrade of the
    BM's own authority.

    Otherwise a user who has switched "acting as" another role is gated on
    that acted-as role instead of their real one. Pass allow_acting_as=False
    for the small set of actions (payment confirmation, invoice finalization)
    that must always require the real role — those still exclude the BM.
    """
    async def _check(current_user: User = Depends(get_current_user)) -> User:
        if allow_acting_as and current_user.role == UserRole.bunker_manager:
            return current_user
        effective_role = get_effective_role(current_user) if allow_acting_as else current_user.role
        if effective_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {[r.value for r in roles]}",
            )
        return current_user
    return _check


def require_operation_manager(allow_acting_as: bool = True):
    """Gate for a Bunker-Manager-level action **on an operation**.

    Accepts the Ops Supervisor alongside the BM. Use this in place of
    `require_roles(UserRole.bunker_manager)` on operation-scoped routes only —
    see app/permissions.py for what that boundary covers and what it excludes.
    """
    return require_roles(*OPERATION_MANAGER_ROLES, allow_acting_as=allow_acting_as)


def get_request_meta(request: Request, current_user: Optional[User] = None) -> dict:
    """Extract IP address, User-Agent, and acting-as state from the request
    for audit logging. Pass the route's already-resolved `current_user` (from
    its own require_roles/get_current_user dependency) — this is a plain
    helper called directly in route bodies, not a FastAPI dependency itself."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    acted_as_role = current_user.acting_as_role.value if current_user and current_user.acting_as_role else None
    return {"ip": ip, "user_agent": user_agent, "acted_as_role": acted_as_role}
