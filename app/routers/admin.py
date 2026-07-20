from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_roles, get_request_meta
from app.models.user import User
from app.models.audit import AuditLog, SystemSetting
from app.models.enums import UserRole
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.user import UserOut, AdminCreateUserRequest, AdminUpdateUserRequest
from app.services.auth_service import AuthService

router = APIRouter(prefix="/admin", tags=["Admin"])

# All admin routes require bunker_manager role
AdminUser = Depends(require_roles(UserRole.bunker_manager))


@router.get("/users", response_model=PaginatedResponse)
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    role: Optional[UserRole] = Query(None),
    is_active: Optional[bool] = Query(None),
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """List all users with optional filters."""
    conditions = []
    if role:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)

    count_stmt = select(func.count()).select_from(User)
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    stmt = select(User).order_by(User.created_at.desc()).offset(offset).limit(per_page)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    users = result.scalars().all()

    items = [UserOut.model_validate(u).model_dump() for u in users]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.get("/clients", response_model=StandardResponse)
async def list_clients(
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """List active client users — for billing pickers (e.g. standalone invoices).

    Deliberately narrower than /admin/users (which is BM-only): Finance needs to
    choose who to bill, but has no business listing staff/admin accounts.
    """
    result = await db.execute(
        select(User)
        .where(and_(User.role == UserRole.client, User.is_active == True))  # noqa: E712
        .order_by(User.full_name)
    )
    clients = result.scalars().all()
    items = [
        {"id": str(c.id), "full_name": c.full_name, "email": c.email}
        for c in clients
    ]
    return StandardResponse.ok(data=items, message="Clients retrieved")


@router.post("/users", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminCreateUserRequest,
    request: Request,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Create a user with any role (admin function).

    No password is set here — the admin never sees or chooses one. The new
    user is emailed a link to set their own password.
    """
    user, email_sent = await AuthService.admin_create_user(
        email=body.email,
        full_name=body.full_name,
        phone=body.phone,
        role=body.role,
        db=db,
    )

    message = (
        f"User {user.email} created — a password-setup email has been sent"
        if email_sent
        else f"User {user.email} created, but the invite email could not be sent "
             f"(check email configuration and resend manually)"
    )
    data = UserOut.model_validate(user).model_dump()
    data["email_sent"] = email_sent
    return StandardResponse.ok(data=data, message=message)


@router.put("/users/{user_id}", response_model=StandardResponse)
async def update_user(
    user_id: UUID,
    body: AdminUpdateUserRequest,
    request: Request,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Update a user's profile or role."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = body.model_dump(exclude_unset=True)

    # Guard against locking the whole org out of BM-gated administration: don't allow
    # demoting or deactivating the last active Bunker Manager.
    demoting = "role" in update_data and update_data["role"] != UserRole.bunker_manager
    deactivating = update_data.get("is_active") is False
    if user.role == UserRole.bunker_manager and (demoting or deactivating):
        active_bm_count = await db.execute(
            select(func.count()).select_from(User).where(
                and_(User.role == UserRole.bunker_manager, User.is_active == True)
            )
        )
        if (active_bm_count.scalar_one() or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot demote or deactivate the last active Bunker Manager.",
            )

    for field, value in update_data.items():
        setattr(user, field, value)
    user.updated_at = datetime.utcnow()

    await db.flush()
    await db.refresh(user)

    return StandardResponse.ok(
        data=UserOut.model_validate(user).model_dump(),
        message="User updated",
    )


@router.get("/audit-logs", response_model=PaginatedResponse)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_id: Optional[UUID] = Query(None),
    actor_email: Optional[str] = Query(None, description="Search by actor email (partial)"),
    operation_id: Optional[UUID] = Query(None),
    action: Optional[str] = Query(None, description="Filter by action keyword"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (api, document, operation, auth…)"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """List audit logs with optional filters. BM only."""
    from sqlalchemy import join as sa_join

    conditions = []
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if operation_id:
        conditions.append(AuditLog.operation_id == operation_id)
    if action:
        conditions.append(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if date_from:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to:
        conditions.append(AuditLog.created_at <= date_to)

    # Base stmt with user join for actor_email search
    base_stmt = (
        select(AuditLog)
        .join(User, AuditLog.user_id == User.id, isouter=True)
        .options(selectinload(AuditLog.user))
        .order_by(AuditLog.created_at.desc())
    )
    count_base = select(func.count()).select_from(AuditLog).join(User, AuditLog.user_id == User.id, isouter=True)

    if actor_email:
        email_cond = User.email.ilike(f"%{actor_email}%")
        conditions.append(email_cond)

    if conditions:
        base_stmt = base_stmt.where(and_(*conditions))
        count_base = count_base.where(and_(*conditions))

    count_result = await db.execute(count_base)
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    result = await db.execute(base_stmt.offset(offset).limit(per_page))
    logs = result.scalars().all()

    items = [
        {
            "id": str(log.id),
            "user_id": str(log.user_id),
            "actor_name": log.user.full_name if log.user else None,
            "actor_email": log.user.email if log.user else None,
            "actor_role": log.user.role.value if log.user else None,
            "actor_acted_as_role": log.acted_as_role.value if log.acted_as_role else None,
            "operation_id": str(log.operation_id) if log.operation_id else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": str(log.entity_id) if log.entity_id else None,
            "changes": log.changes,
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]

    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.get("/settings", response_model=StandardResponse)
async def get_settings(
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all system settings."""
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.key))
    settings_list = result.scalars().all()

    items = [
        {
            "key": s.key,
            "value": s.value,
            "description": s.description,
            "updated_by": str(s.updated_by) if s.updated_by else None,
            "updated_at": s.updated_at.isoformat(),
        }
        for s in settings_list
    ]

    return StandardResponse.ok(data=items, message="Settings retrieved")


@router.put("/settings/{key}", response_model=StandardResponse)
async def update_setting(
    key: str,
    body: dict,
    request: Request,
    current_user: User = AdminUser,
    db: AsyncSession = Depends(get_db),
):
    """Update a system setting value."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()

    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Setting '{key}' not found")

    # Never let an admin overwrite the sequence counters (seq_operation_*, seq_bdn_*,
    # seq_pfi_*, …) — editing them would corrupt document/operation numbering.
    if key.startswith("seq_"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Sequence counters are managed by the system and cannot be edited.",
        )

    new_value = body.get("value")
    if new_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain a 'value' field",
        )

    setting.value = new_value
    setting.updated_by = current_user.id
    setting.updated_at = datetime.utcnow()

    await db.flush()

    return StandardResponse.ok(
        data={
            "key": setting.key,
            "value": setting.value,
            "description": setting.description,
            "updated_at": setting.updated_at.isoformat(),
        },
        message=f"Setting '{key}' updated",
    )
