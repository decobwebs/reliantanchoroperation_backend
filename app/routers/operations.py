from typing import Optional
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles, get_request_meta
from app.models.user import User
from app.models.enums import UserRole, OperationStatus, OperationType
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.operation import (
    CreateOperationRequest, UpdateOperationRequest, TransitionRequest,
    PauseRequest, ResumeRequest, OperationOut, StatusHistoryOut, OperationFilters,
    ReopenRequest,
)
from app.schemas.truck import VesselDischargeEventCreate, VesselDischargeEventOut
from app.services.operation_service import OperationService
from app.models.audit import AuditLog
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/operations", tags=["Operations"])


@router.get("", response_model=PaginatedResponse)
async def list_operations(
    request: Request,
    status_filter: Optional[OperationStatus] = Query(None, alias="status"),
    type_filter: Optional[OperationType] = Query(None, alias="type"),
    client_id: Optional[UUID] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List operations with pagination and filters.
    Results are scoped based on the authenticated user's role.
    """
    filters = OperationFilters(
        status=status_filter,
        type=type_filter,
        client_id=client_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )

    operations, total = await OperationService.list_operations(filters, current_user, db)
    items = [OperationOut.model_validate(op).model_dump() for op in operations]

    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.post("", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_operation(
    body: CreateOperationRequest,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new operation. Only bunker_managers can create operations."""
    meta = get_request_meta(request)
    operation = await OperationService.create_operation(body, current_user, db, meta)

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message=f"Operation {operation.operation_number} created",
    )


@router.get("/{operation_id}", response_model=StandardResponse)
async def get_operation(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single operation by ID."""
    operation = await OperationService.get_operation(operation_id, current_user, db)

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message="Operation retrieved",
    )


@router.put("/{operation_id}", response_model=StandardResponse)
async def update_operation(
    operation_id: UUID,
    body: UpdateOperationRequest,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Update an operation's editable fields. Only bunker_managers."""
    meta = get_request_meta(request)
    operation = await OperationService.update_operation(operation_id, body, current_user, db, meta)

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message="Operation updated",
    )


@router.post("/{operation_id}/transition", response_model=StandardResponse)
async def transition_operation(
    operation_id: UUID,
    body: TransitionRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Transition an operation to a new status.
    Permissions are validated against the state machine's TRANSITION_PERMISSIONS map.
    """
    meta = get_request_meta(request)
    operation = await OperationService.transition_operation(
        operation_id, body, current_user, db, meta
    )

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message=f"Operation transitioned to '{operation.status.value}'",
    )


@router.post("/{operation_id}/pause", response_model=StandardResponse)
async def pause_operation(
    operation_id: UUID,
    body: PauseRequest,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Pause an active operation."""
    meta = get_request_meta(request)
    operation = await OperationService.pause_operation(
        operation_id, body.reason, current_user, db, meta
    )

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message="Operation paused",
    )


@router.post("/{operation_id}/resume", response_model=StandardResponse)
async def resume_operation(
    operation_id: UUID,
    request: Request,
    body: ResumeRequest = Body(default=ResumeRequest()),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused operation."""
    meta = get_request_meta(request)
    operation = await OperationService.resume_operation(
        operation_id, body.reason, current_user, db, meta
    )

    return StandardResponse.ok(
        data=OperationOut.model_validate(operation).model_dump(),
        message="Operation resumed",
    )


@router.get("/{operation_id}/timeline", response_model=StandardResponse)
async def get_operation_timeline(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the full status transition timeline for an operation."""
    history = await OperationService.get_timeline(operation_id, current_user, db)

    timeline = []
    for entry in history:
        timeline.append({
            "id": str(entry.id),
            "operation_id": str(entry.operation_id),
            "from_status": entry.from_status.value if entry.from_status else None,
            "to_status": entry.to_status.value,
            "changed_by": str(entry.changed_by),
            "reason": entry.reason,
            "metadata": entry.metadata_,
            "created_at": entry.created_at.isoformat(),
        })

    return StandardResponse.ok(data=timeline, message="Timeline retrieved")


@router.delete("/{operation_id}", response_model=StandardResponse)
async def delete_operation(
    operation_id: UUID,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete an operation. Only permitted for draft operations."""
    meta = get_request_meta(request)
    await OperationService.soft_delete_operation(operation_id, current_user, db, meta)

    return StandardResponse.ok(message="Operation deleted")


@router.post("/{operation_id}/reopen", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def reopen_operation(
    operation_id: UUID,
    body: ReopenRequest,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Create a new revision of a completed/archived/cancelled operation. BM only."""
    meta = get_request_meta(request)
    new_op = await OperationService.reopen_operation(operation_id, body, current_user, db, meta)
    return StandardResponse.ok(
        data=OperationOut.model_validate(new_op).model_dump(),
        message=f"Revision {new_op.version} created: {new_op.operation_number}",
    )


@router.get("/{operation_id}/versions", response_model=StandardResponse)
async def list_operation_versions(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all versions (revisions) of an operation family."""
    from sqlalchemy import or_
    from app.models.operation import Operation

    # Find root
    result = await db.execute(select(Operation).where(Operation.id == operation_id))
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")

    root_id = op.parent_operation_id or op.id
    stmt = (
        select(Operation)
        .where(or_(Operation.id == root_id, Operation.parent_operation_id == root_id))
        .order_by(Operation.version.asc())
    )
    all_versions = (await db.execute(stmt)).scalars().all()
    return StandardResponse.ok(
        data=[OperationOut.model_validate(v).model_dump() for v in all_versions],
        message=f"{len(all_versions)} version(s) found",
    )


@router.post("/{operation_id}/vessel-discharges", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_vessel_discharge_event(
    operation_id: UUID,
    body: VesselDischargeEventCreate,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.ops_supervisor, UserRole.marine_manager, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record a vessel-to-vessel (or vessel-to-client) discharge event."""
    from app.services.vessel_discharge_service import VesselDischargeService
    meta = get_request_meta(request)
    event = await VesselDischargeService.create_event(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselDischargeEventOut.model_validate(event).model_dump(),
        message="Vessel discharge event recorded",
    )


@router.get("/{operation_id}/vessel-discharges", response_model=StandardResponse)
async def list_vessel_discharge_events(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all vessel discharge events for an operation."""
    from app.models.bdn import VesselDischargeEvent
    result = await db.execute(
        select(VesselDischargeEvent)
        .where(VesselDischargeEvent.operation_id == operation_id)
        .order_by(VesselDischargeEvent.created_at.asc())
    )
    events = result.scalars().all()
    return StandardResponse.ok(
        data=[VesselDischargeEventOut.model_validate(e).model_dump() for e in events],
        message=f"{len(events)} discharge event(s)",
    )


@router.get("/{operation_id}/audit-log", response_model=StandardResponse)
async def get_operation_audit_log(
    operation_id: UUID,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Return the full audit trail for an operation. Bunker Manager only."""
    result = await db.execute(
        select(AuditLog)
        .options(joinedload(AuditLog.user))
        .where(AuditLog.operation_id == operation_id)
        .order_by(AuditLog.created_at.asc())
    )
    logs = result.scalars().all()
    items = [
        {
            "id": str(log.id),
            "user_id": str(log.user_id),
            "user_name": log.user.full_name if log.user else "Unknown",
            "user_role": log.user.role.value if log.user else "",
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": str(log.entity_id) if log.entity_id else None,
            "changes": log.changes,
            "reason": log.reason,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
    return StandardResponse.ok(data=items, message="Audit log retrieved")
