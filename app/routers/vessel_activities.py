"""
Vessel Activity endpoints — Marine Supervisor oversight sessions.
BM creates/assigns; Marine Supervisor records quantities and completes.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.vessel_activity import (
    VesselActivityCreate,
    VesselActivityRecordReceipt,
    VesselActivityRecordBunkering,
    VesselActivityRecordDischarge,
    VesselActivityComplete,
    VesselActivityPatchInitialRob,
    VesselActivityOut,
)
from app.services.vessel_activity_service import VesselActivityService

router = APIRouter(tags=["Vessel Activities"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))
_marine_only = Depends(require_roles(UserRole.marine_manager))
_bm_marine = Depends(require_roles(UserRole.bunker_manager, UserRole.marine_manager))


# ── Operation-scoped ───────────────────────────────────────────────────────────

@router.post(
    "/operations/{operation_id}/vessel-activities",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_vessel_activity(
    operation_id: UUID,
    body: VesselActivityCreate,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Assign a Marine Supervisor to oversee vessel bunkering/discharge. Bunker Manager only."""
    activity = await VesselActivityService.create(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message=f"Vessel activity {activity.activity_number} assigned",
    )


@router.get("/operations/{operation_id}/vessel-activities", response_model=StandardResponse)
async def list_vessel_activities(
    operation_id: UUID,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """List all vessel activities for an operation. BM and Marine Manager."""
    activities = await VesselActivityService.list_by_operation(operation_id, db)
    items = [VesselActivityOut.model_validate(a).model_dump() for a in activities]
    return StandardResponse.ok(data=items, message="Vessel activities retrieved")


# ── My activities (must be before /{activity_id} to avoid UUID parse on "my") ──

@router.get("/vessel-activities/my/assigned", response_model=StandardResponse)
async def list_my_vessel_activities(
    current_user: User = _marine_only,
    db: AsyncSession = Depends(get_db),
):
    """List all vessel activities assigned to the current Marine Manager."""
    activities = await VesselActivityService.list_assigned_to(current_user.id, db)
    items = [VesselActivityOut.model_validate(a).model_dump() for a in activities]
    return StandardResponse.ok(data=items, message="Your vessel activities retrieved")


# ── Individual activity lifecycle ──────────────────────────────────────────────

@router.get("/vessel-activities/{activity_id}", response_model=StandardResponse)
async def get_vessel_activity(
    activity_id: UUID,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Get a single vessel activity by ID. BM and Marine Manager."""
    activity = await VesselActivityService.get(activity_id, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Vessel activity retrieved",
    )


@router.post("/vessel-activities/{activity_id}/start", response_model=StandardResponse)
async def start_vessel_activity(
    activity_id: UUID,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Start a pending vessel activity. Assigned Marine Manager or BM."""
    activity = await VesselActivityService.start(activity_id, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Vessel activity started",
    )


@router.post("/vessel-activities/{activity_id}/record-receipt", response_model=StandardResponse)
async def record_receipt(
    activity_id: UUID,
    body: VesselActivityRecordReceipt,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Record vessel receipt quantities and compute ROB. Marine Manager or BM."""
    activity = await VesselActivityService.record_receipt(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Receipt quantities recorded",
    )


@router.post("/vessel-activities/{activity_id}/record-bunkering", response_model=StandardResponse)
async def record_bunkering(
    activity_id: UUID,
    body: VesselActivityRecordBunkering,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Record bunkering start/end timestamps. Marine Manager or BM."""
    activity = await VesselActivityService.record_bunkering(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Bunkering timing recorded",
    )


@router.post("/vessel-activities/{activity_id}/record-discharge", response_model=StandardResponse)
async def record_discharge(
    activity_id: UUID,
    body: VesselActivityRecordDischarge,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Record discharge quantity and compute final ROB. Marine Manager or BM."""
    activity = await VesselActivityService.record_discharge(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Discharge recorded",
    )


@router.post("/vessel-activities/{activity_id}/complete", response_model=StandardResponse)
async def complete_vessel_activity(
    activity_id: UUID,
    body: VesselActivityComplete = VesselActivityComplete(),
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    """Complete a vessel activity, update vessel ROB, write ledger entry. Marine Manager or BM."""
    activity = await VesselActivityService.complete(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message=f"Vessel activity {activity.activity_number} completed",
    )


@router.patch("/vessel-activities/{activity_id}/initial-rob", response_model=StandardResponse)
async def patch_initial_rob(
    activity_id: UUID,
    body: VesselActivityPatchInitialRob,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Edit the pre-operation Initial ROB. Bunker Manager only. Action is audit-logged."""
    activity = await VesselActivityService.patch_initial_rob(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Initial ROB updated",
    )


@router.post("/vessel-activities/{activity_id}/cancel", response_model=StandardResponse)
async def cancel_vessel_activity(
    activity_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a vessel activity. Bunker Manager only."""
    activity = await VesselActivityService.cancel(activity_id, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message="Vessel activity cancelled",
    )
