from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.dependencies import get_current_user, get_request_meta
from app.models.user import User
from app.models.enums import UserRole
from app.models.audit import AuditLog
from app.schemas.auth import (
    RegisterRequest, LoginRequest, RefreshRequest, LogoutRequest,
    ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest,
    UpdateMeRequest, TokenResponse,
)
from app.schemas.user import UserOut
from app.schemas.common import StandardResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user. By default, role is set to 'client'.
    Only a bunker_manager can register users with elevated roles
    (enforced at service level — public registration always results in client role).
    """
    # Public registration always creates clients; role override is admin-only
    role = UserRole.client

    user = await AuthService.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
        role=role,
        db=db,
    )

    return StandardResponse.ok(
        data=UserOut.model_validate(user).model_dump(),
        message="Registration successful",
    )


@router.post("/bootstrap", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    One-time endpoint to create the first Bunker Manager.
    Returns 403 if any bunker_manager already exists in the system.
    This is a bootstrap-only route — use /admin/users for all subsequent user creation.
    """
    from sqlalchemy import func as sql_func
    existing = await db.execute(
        select(sql_func.count()).select_from(User).where(User.role == UserRole.bunker_manager)
    )
    if existing.scalar_one() > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bootstrap unavailable: a Bunker Manager already exists. Use /admin/users.",
        )

    user = await AuthService.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        phone=body.phone,
        role=UserRole.bunker_manager,
        db=db,
    )

    return StandardResponse.ok(
        data=UserOut.model_validate(user).model_dump(),
        message="First Bunker Manager created. This endpoint is now disabled.",
    )


@router.post("/login", response_model=StandardResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user and return Supabase JWT tokens."""
    token_data = await AuthService.login(body.email, body.password)

    # Update last_login_at and log the login event
    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if user:
        user.last_login_at = datetime.utcnow()
        meta = get_request_meta(request)
        db.add(AuditLog(
            user_id=user.id,
            action="LOGIN",
            entity_type="auth",
            changes={"email": user.email, "role": user.role.value},
            ip_address=meta["ip"],
            user_agent=meta["user_agent"],
        ))
        await db.flush()

    response = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_type": "bearer",
        "expires_in": token_data.get("expires_in"),
        "user": UserOut.model_validate(user).model_dump() if user else None,
    }

    return StandardResponse.ok(data=response, message="Login successful")


@router.post("/refresh", response_model=StandardResponse)
async def refresh(body: RefreshRequest):
    """Refresh access token using a valid refresh token."""
    token_data = await AuthService.refresh_token(body.refresh_token)

    return StandardResponse.ok(
        data={
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": "bearer",
            "expires_in": token_data.get("expires_in"),
        },
        message="Token refreshed",
    )


@router.post("/logout", response_model=StandardResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Invalidate the user's session."""
    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.replace("Bearer ", "").strip()
    if access_token:
        await AuthService.logout(access_token)

    return StandardResponse.ok(message="Logged out successfully")


@router.get("/me", response_model=StandardResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return StandardResponse.ok(
        data=UserOut.model_validate(current_user).model_dump(),
        message="Profile retrieved",
    )


@router.put("/me", response_model=StandardResponse)
async def update_me(
    body: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the currently authenticated user's profile."""
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)
    current_user.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(current_user)

    return StandardResponse.ok(
        data=UserOut.model_validate(current_user).model_dump(),
        message="Profile updated",
    )


@router.post("/change-password", response_model=StandardResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Change the authenticated user's password."""
    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.replace("Bearer ", "").strip()

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No access token found in request",
        )

    await AuthService.change_password(access_token, body.new_password)
    return StandardResponse.ok(message="Password changed successfully")


@router.post("/forgot-password", response_model=StandardResponse)
async def forgot_password(body: ForgotPasswordRequest):
    """Send a password reset email."""
    await AuthService.forgot_password(body.email)
    return StandardResponse.ok(
        message="If an account with that email exists, a reset link has been sent"
    )


@router.post("/reset-password", response_model=StandardResponse)
async def reset_password(body: ResetPasswordRequest):
    """Reset password using a recovery token from email."""
    await AuthService.reset_password(body.token, body.new_password)
    return StandardResponse.ok(message="Password reset successfully")
