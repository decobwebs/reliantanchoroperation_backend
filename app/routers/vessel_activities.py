"""
Vessel Activity endpoints — Marine Supervisor oversight sessions.
BM creates/assigns; Marine Supervisor records quantities and completes.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
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
    AdvanceStageRequest,
    AddCommentRequest,
    VesselActivityCommentOut,
    RecordHseRequest,
    SetCastOffContactsRequest,
    RecordDischargeQuantitiesRequest,
    VesselActivityCommenceRequest,
    VesselActivityCompleteVesselOpRequest,
    VesselActivityUpdateOut,
    RecordVesselOperationQuantitiesRequest,
    RecordLoadingReceiptRequest,
    VesselActivityCorrectTimingRequest,
    EditVesselActivityCommentRequest,
    UncancelRequest,
)
from app.schemas.vessel_activity_leg import (
    VesselActivityLegCreate,
    AdvanceLegStageRequest,
    RecordLegHseRequest,
    RecordLegQuantitiesRequest,
    CorrectLegTimingRequest,
    CancelLegRequest,
    EditVesselActivityLegRequest,
    VesselActivityLegOut,
    SetLegAdhocClientRequest,
)
from app.services.vessel_activity_service import VesselActivityService

router = APIRouter(tags=["Vessel Activities"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))
_marine_only = Depends(require_roles(UserRole.cargo_superintendent))
_bm_marine = Depends(require_roles(UserRole.bunker_manager, UserRole.cargo_superintendent))
# Stage progression: spec assigns this to "Marine / Ops Supervisor".
_stage_roles = Depends(require_roles(UserRole.bunker_manager, UserRole.cargo_superintendent, UserRole.ops_supervisor))
# HSE: no dedicated Safety Officer role exists yet — folds into Ops
# Supervisor + BM for now (widening to a real role later is additive).
_hse_roles = Depends(require_roles(UserRole.bunker_manager, UserRole.ops_supervisor))


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


# ── Per-vessel stage flow ────────────────────────────────────────────────────

@router.post("/vessel-activities/{activity_id}/advance-stage", response_model=StandardResponse)
async def advance_stage(
    activity_id: UUID,
    body: AdvanceStageRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Log (or correct) a stage timestamp — cast_off through discharge_completed.
    Timestamp is always caller-supplied, never forced to "now"."""
    activity = await VesselActivityService.advance_stage(activity_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message=f"Stage '{body.stage.value}' recorded",
    )


@router.post("/vessel-activities/{activity_id}/comments", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(
    activity_id: UUID,
    body: AddCommentRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """A free-text comment not tied to logging a stage transition."""
    comment = await VesselActivityService.add_comment(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityCommentOut.model_validate(comment).model_dump(), message="Comment added")


@router.get("/vessel-activities/{activity_id}/comments", response_model=StandardResponse)
async def list_comments(
    activity_id: UUID,
    current_user: User = _bm_marine,
    db: AsyncSession = Depends(get_db),
):
    comments = await VesselActivityService.list_comments(activity_id, db)
    items = [VesselActivityCommentOut.model_validate(c).model_dump() for c in comments]
    return StandardResponse.ok(data=items, message="Comments retrieved")


@router.post("/vessel-activities/{activity_id}/hse", response_model=StandardResponse)
async def record_hse(
    activity_id: UUID,
    body: RecordHseRequest,
    current_user: User = _hse_roles,
    db: AsyncSession = Depends(get_db),
):
    """Non-blocking HSE safety checklist — a failed item is recorded, never enforced.

    `body.phase` selects which of the three checks this is (pre / during /
    post); it defaults to "pre", which is the check this endpoint recorded
    before the split, so older callers are unaffected.
    """
    activity = await VesselActivityService.record_hse(activity_id, body, current_user, db)
    _PHASE_LABEL = {"pre": "Pre-Operation", "during": "During Operation", "post": "Post-Operation"}
    return StandardResponse.ok(
        data=VesselActivityOut.model_validate(activity).model_dump(),
        message=f"{_PHASE_LABEL[body.phase]} HSE checklist recorded",
    )


@router.patch("/vessel-activities/{activity_id}/cast-off-contacts", response_model=StandardResponse)
async def set_cast_off_contacts(
    activity_id: UUID,
    body: SetCastOffContactsRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Client name, client's vessel name, and email recipients for this run.

    Captured at Cast Off and editable afterwards. Recording contacts never
    sends anything — mail goes out only after the BM approves and then sends.
    """
    activity = await VesselActivityService.set_cast_off_contacts(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Client contacts saved")


@router.post("/vessel-activities/{activity_id}/discharge-quantities", response_model=StandardResponse)
async def record_discharge_quantities(
    activity_id: UUID,
    body: RecordDischargeQuantitiesRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Submits GOV/VCF/density readings — the system computes GSV and MTvac."""
    activity = await VesselActivityService.record_discharge_quantities(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Discharge quantities recorded")


# ── Vessel-only: commence -> updates -> complete -> quantities ─────────────────
# Fully separate from the stage flow above — applies only to vessel_only
# operations (the service rejects the call otherwise). Same _stage_roles as
# advance-stage/hse/discharge-quantities: MM/OS write, BM retains full
# visibility and correction power.

@router.post("/vessel-activities/{activity_id}/commence", response_model=StandardResponse)
async def commence_vessel_operation(
    activity_id: UUID,
    body: VesselActivityCommenceRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Commence a vessel-only operation. Captures both the system instant and
    the caller's own stated commencement time — both stored, both displayed."""
    activity = await VesselActivityService.commence(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Vessel operation commenced")


@router.post("/vessel-activities/{activity_id}/updates", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def add_vessel_activity_update(
    activity_id: UUID,
    content: str = Form(...),
    image: Optional[UploadFile] = File(None),
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Free-form operational update — content + optional image, always
    system-timestamped only."""
    image_bytes = await image.read() if image else None
    update = await VesselActivityService.add_update(
        activity_id, content, image_bytes,
        image.filename if image else None, image.content_type if image else None,
        current_user, db,
    )
    return StandardResponse.ok(data=VesselActivityUpdateOut.model_validate(update).model_dump(), message="Update added")


@router.get("/vessel-activities/{activity_id}/updates", response_model=StandardResponse)
async def list_vessel_activity_updates(
    activity_id: UUID,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    updates = await VesselActivityService.list_updates(activity_id, db)
    items = [VesselActivityUpdateOut.model_validate(u).model_dump() for u in updates]
    return StandardResponse.ok(data=items, message="Updates retrieved")


@router.post("/vessel-activities/{activity_id}/complete-vessel-operation", response_model=StandardResponse)
async def complete_vessel_operation(
    activity_id: UUID,
    body: VesselActivityCompleteVesselOpRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Complete a vessel-only operation — this, not quantities, is what
    unlocks Vessel BDN submission eligibility."""
    activity = await VesselActivityService.complete_vessel_operation(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Vessel operation completed")


@router.post("/vessel-activities/{activity_id}/quantities", response_model=StandardResponse)
async def record_vessel_operation_quantities(
    activity_id: UUID,
    body: RecordVesselOperationQuantitiesRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Discharge & Received Quantity — a separate operational note, not a
    BDN precondition. Also updates the vessel's ROB (Received adds,
    Discharged subtracts). Resubmission requires Bunker Manager + reason."""
    activity = await VesselActivityService.record_quantities(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Quantities recorded")


@router.patch("/vessel-activities/{activity_id}/vessel-operation-timing", response_model=StandardResponse)
async def correct_vessel_operation_timing(
    activity_id: UUID,
    body: VesselActivityCorrectTimingRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Correct any of the four commence/complete (Loading Commenced/
    Completed) timings. Bunker Manager only, reason required."""
    activity = await VesselActivityService.correct_timing(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Timing corrected")


@router.post("/vessel-activities/{activity_id}/loading-receipt", response_model=StandardResponse)
async def record_loading_receipt(
    activity_id: UUID,
    body: RecordLoadingReceiptRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """One-time Received Quantity + quality readings at the loading step.
    Resubmission requires Bunker Manager + reason."""
    activity = await VesselActivityService.record_loading_receipt(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Loading receipt recorded")


# ── Receiving-vessel legs — one per receiving vessel, added at any point ───────
# Delivery repeats per receiving vessel: each leg runs its own Cast Off ->
# Alongside -> Discharge Commenced -> Discharge Completed sequence.

@router.post("/vessel-activities/{activity_id}/legs", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def add_vessel_activity_leg(
    activity_id: UUID,
    body: VesselActivityLegCreate,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Register a new receiving vessel. Bunker Manager only — the master
    spec names the BM specifically for this action. No gate on Loading
    Completed; can be added at any point, including after other legs on
    the same activity already finished."""
    leg = await VesselActivityService.add_leg(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Receiving vessel added")


@router.get("/vessel-activities/{activity_id}/legs", response_model=StandardResponse)
async def list_vessel_activity_legs(
    activity_id: UUID,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    legs = await VesselActivityService.list_legs(activity_id, db)
    items = [VesselActivityLegOut.model_validate(leg).model_dump() for leg in legs]
    return StandardResponse.ok(data=items, message="Receiving vessels retrieved")


@router.post("/vessel-activity-legs/{leg_id}/advance-stage", response_model=StandardResponse)
async def advance_leg_stage(
    leg_id: UUID,
    body: AdvanceLegStageRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Log (or correct) a leg stage timestamp — cast_off through
    discharge_completed. Both the system instant and the caller's stated
    time are stored, never one overwriting the other."""
    leg = await VesselActivityService.advance_leg_stage(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message=f"Leg stage '{body.stage.value}' recorded")


@router.post("/vessel-activity-legs/{leg_id}/hse", response_model=StandardResponse)
async def record_leg_hse(
    leg_id: UUID,
    body: RecordLegHseRequest,
    current_user: User = _hse_roles,
    db: AsyncSession = Depends(get_db),
):
    """Non-blocking HSE safety checklist for one receiving-vessel leg."""
    leg = await VesselActivityService.record_leg_hse(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="HSE checklist recorded")


@router.post("/vessel-activity-legs/{leg_id}/quantities", response_model=StandardResponse)
async def record_leg_quantities(
    leg_id: UUID,
    body: RecordLegQuantitiesRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Discharge quantity + quality readings for one receiving-vessel leg,
    recorded once that leg reaches Discharge Completed. Updates the
    vessel's ROB ledger (subtracts). Resubmission requires Bunker Manager
    + reason."""
    leg = await VesselActivityService.record_leg_quantities(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Quantities recorded")


@router.patch("/vessel-activity-legs/{leg_id}/timing", response_model=StandardResponse)
async def correct_leg_timing(
    leg_id: UUID,
    body: CorrectLegTimingRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Correct any of the eight leg stage timings. Bunker Manager only, reason required."""
    leg = await VesselActivityService.correct_leg_timing(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Leg timing corrected")


@router.post("/vessel-activity-legs/{leg_id}/cancel", response_model=StandardResponse)
async def cancel_leg(
    leg_id: UUID,
    body: CancelLegRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a receiving-vessel leg. Bunker Manager only, reason required."""
    leg = await VesselActivityService.cancel_leg(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Receiving vessel cancelled")


# ── BM corrections ────────────────────────────────────────────────────────────
# The Bunker Manager can correct any recorded detail of an operation. Every
# one of these is BM-only and requires a reason, which is audit-logged.
# Nothing is deletable — a correction edits the record and marks it as edited.

@router.patch("/vessel-activity-updates/{update_id}", response_model=StandardResponse)
async def edit_vessel_activity_update(
    update_id: UUID,
    reason: str = Form(...),
    content: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Correct a posted update's text and/or replace its image."""
    if not reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required to correct an update")
    image_bytes = await image.read() if image else None
    update = await VesselActivityService.edit_update(
        update_id, content, reason.strip(), image_bytes,
        image.filename if image else None, image.content_type if image else None,
        current_user, db,
    )
    return StandardResponse.ok(data=VesselActivityUpdateOut.model_validate(update).model_dump(), message="Update corrected")


@router.patch("/vessel-activity-comments/{comment_id}", response_model=StandardResponse)
async def edit_vessel_activity_comment(
    comment_id: UUID,
    body: EditVesselActivityCommentRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Correct a posted comment."""
    comment = await VesselActivityService.edit_comment(comment_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityCommentOut.model_validate(comment).model_dump(), message="Comment corrected")


@router.patch("/vessel-activity-legs/{leg_id}/adhoc-client", response_model=StandardResponse)
async def set_leg_adhoc_client(
    leg_id: UUID,
    body: SetLegAdhocClientRequest,
    current_user: User = _stage_roles,
    db: AsyncSession = Depends(get_db),
):
    """Capture an ad-hoc client contact for a receiving vessel with no
    registered client account. Capture only — no send action in this
    build. Only settable once the leg has reached Cast Off."""
    leg = await VesselActivityService.set_leg_adhoc_client(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Client contact saved")


@router.patch("/vessel-activity-legs/{leg_id}", response_model=StandardResponse)
async def edit_vessel_activity_leg(
    leg_id: UUID,
    body: EditVesselActivityLegRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Correct a receiving vessel's name, IMO number or ETA."""
    leg = await VesselActivityService.edit_leg(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Receiving vessel updated")


@router.post("/vessel-activity-legs/{leg_id}/uncancel", response_model=StandardResponse)
async def uncancel_vessel_activity_leg(
    leg_id: UUID,
    body: UncancelRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Restore a cancelled receiving vessel. Re-derives the completion gate."""
    leg = await VesselActivityService.uncancel_leg(leg_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityLegOut.model_validate(leg).model_dump(), message="Receiving vessel restored")


@router.post("/vessel-activities/{activity_id}/uncancel", response_model=StandardResponse)
async def uncancel_vessel_activity(
    activity_id: UUID,
    body: UncancelRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Restore a cancelled vessel activity to the point it had reached."""
    activity = await VesselActivityService.uncancel(activity_id, body, current_user, db)
    return StandardResponse.ok(data=VesselActivityOut.model_validate(activity).model_dump(), message="Vessel activity restored")
