"""
Vessel Activity service — manages marine supervisor oversight sessions.
Lifecycle: pending → active → completed (or cancelled)
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import selectinload

from decimal import Decimal

from app.models.bdn import BDN, VesselActivity, VesselActivityLeg, RobEntry, VesselActivityComment, VesselActivityUpdate
from app.models.vessel import Vessel
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import VesselActivityStatus, RobEntryType, UserRole, VesselStage, VesselLegStage, OperationStatus, OperationType, BdnStatus
from app.schemas.vessel_activity import (
    VesselActivityCreate,
    VesselActivityRecordReceipt,
    VesselActivityRecordBunkering,
    VesselActivityRecordDischarge,
    VesselActivityComplete,
    VesselActivityPatchInitialRob,
    AdvanceStageRequest,
    AddCommentRequest,
    SetCastOffContactsRequest,
    RecordHseRequest,
    RecordDischargeQuantitiesRequest,
    VesselActivityCommenceRequest,
    VesselActivityCompleteVesselOpRequest,
    AddVesselActivityUpdateRequest,
    RecordVesselOperationQuantitiesRequest,
    RecordLoadingReceiptRequest,
    VesselActivityCorrectTimingRequest,
    EditVesselActivityUpdateRequest,
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
    SetLegAdhocClientRequest,
)
from app.services.audit_diff import capture_diff
from app.services.state_machine import StateMachine, StateMachineError, acting_role
from app.utils.number_generator import generate_vessel_activity_number

# Marker distinguishing the new commence/complete flow's RobEntry rows from
# the old complete()'s, so a quantities correction can find and reverse
# exactly its own pair without touching anything else.
_QUANTITIES_ROB_SOURCE_SUFFIX = " — quantities"

# Same idea, scoped to the six-stage + receiving-vessel-legs rebuild's two
# independent ROB writers — kept distinct from each other and from the
# suffix above so a correction on one never touches another's ledger rows.
_LOADING_RECEIPT_ROB_SOURCE_SUFFIX = " — loading receipt"
_LEG_QUANTITIES_ROB_SOURCE_SUFFIX = " — leg quantities"

# Stage order — used to validate forward progression (BM may still correct
# an already-logged stage; this only stops skipping ahead by mistake).
_STAGE_ORDER = [
    VesselStage.cast_off, VesselStage.approach, VesselStage.alongside,
    VesselStage.hse_check, VesselStage.commence_discharge, VesselStage.discharge_completed,
]

# Delivery-leg stage order — separate 4-entry sequence for receiving-vessel
# legs (see VesselLegStage). Cast Off additionally requires Loading
# Completed on the parent activity (checked in advance_leg_stage).
_LEG_STAGE_ORDER = [
    VesselLegStage.cast_off, VesselLegStage.alongside,
    VesselLegStage.discharge_commenced, VesselLegStage.discharge_completed,
]


def _attach_vessel_name(activity: VesselActivity) -> None:
    """Attach non-mapped vessel/comment-author/update-author fields onto the
    activity for schema serialisation. Requires `.vessel`, `.comments.recorder`,
    and `.updates.recorder` to already be eager-loaded on `activity` (see
    _get_or_404/list_* queries) — accessing them here otherwise triggers a
    lazy-load that fails in this async context."""
    vessel = getattr(activity, 'vessel', None)
    activity.vessel_name = vessel.vessel_name if vessel else None  # type: ignore[attr-defined]
    activity.vessel_current_rob_mt = vessel.current_rob_mt if vessel else None  # type: ignore[attr-defined]
    for c in getattr(activity, 'comments', None) or []:
        c.recorded_by_name = c.recorder.full_name if getattr(c, 'recorder', None) else None  # type: ignore[attr-defined]
    for u in getattr(activity, 'updates', None) or []:
        u.recorded_by_name = u.recorder.full_name if getattr(u, 'recorder', None) else None  # type: ignore[attr-defined]


async def _transition_operation(
    operation: Operation,
    to_status: OperationStatus,
    current_user: User,
    db: AsyncSession,
    reason: str = "",
) -> None:
    if operation.status == to_status:
        return
    try:
        StateMachine.validate_transition(
            operation.type, operation.status, to_status, acting_role(current_user)
        )
    except StateMachineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    from_status = operation.status
    operation.status = to_status
    operation.updated_at = datetime.utcnow()

    db.add(OperationStatusHistory(
        operation_id=operation.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=current_user.id,
        reason=reason,
        metadata_={},
    ))


class VesselActivityService:

    @staticmethod
    async def _create_activity_row(
        op: Operation,
        vessel: Vessel,
        assigned_to: UUID,
        assigned_by: UUID,
        notes: Optional[str],
        db: AsyncSession,
    ) -> VesselActivity:
        """Core row-insert + audit + notify, shared by the BM's manual 'Assign
        Activity' action and the operation-creation auto-commence path. Does
        not commit — caller owns the transaction boundary."""
        activity_number = await generate_vessel_activity_number(db)
        activity = VesselActivity(
            activity_number=activity_number,
            operation_id=op.id,
            vessel_id=vessel.id,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            notes=notes,
            status=VesselActivityStatus.pending,
            initial_rob_mt=vessel.current_rob_mt,
        )
        db.add(activity)
        await db.flush()

        db.add(AuditLog(
            user_id=assigned_by,
            operation_id=op.id,
            action="CREATE_VESSEL_ACTIVITY",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "activity_number": activity_number,
                "vessel_id": str(vessel.id),
                "assigned_to": str(assigned_to),
            },
        ))

        from app.services.notification_service import notify
        await notify(
            db=db,
            user_id=assigned_to,
            type_="vessel_activity_assigned",
            title=f"Vessel Activity Assigned — {activity_number}",
            message=(
                f"You have been assigned to oversee vessel bunkering/discharge for "
                f"operation {op.operation_number} aboard {vessel.vessel_name}. "
                f"Please initiate the activity and begin recording."
            ),
            priority="high",
            operation_id=op.id,
            action_url=f"/operations/{op.id}",
            channels=["in_app", "whatsapp"],
            wa_template="task_assigned",
            wa_kwargs={
                "operation_number": op.operation_number,
                "task": f"Vessel activity {activity_number} — {vessel.vessel_name}",
            },
        )
        return activity

    @staticmethod
    async def create(
        operation_id: UUID,
        data: VesselActivityCreate,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        op = await db.get(Operation, operation_id)
        if not op or op.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        vessel = await db.get(Vessel, data.vessel_id)
        if not vessel or not vessel.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found or inactive")

        assignee = await db.get(User, data.assigned_to)
        if not assignee or assignee.role != UserRole.cargo_superintendent:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assigned user must be a Marine Manager",
            )

        # BM adding another vessel after others already finished — keep the
        # operation's own status honest without a manual fixup step. An
        # operation isn't always discharging to just one vessel, and the
        # BM may only realise a second run is needed after the first one's
        # BDN is already approved — bdn_approved -> vessel_operations is
        # already a valid, BM-permitted transition (state_machine.py), this
        # just wasn't triggering it.
        if op.status in (OperationStatus.pending_completion, OperationStatus.bdn_pending, OperationStatus.bdn_approved):
            await _transition_operation(op, OperationStatus.vessel_operations, current_user, db, reason="New vessel activity added")

        activity = await VesselActivityService._create_activity_row(
            op, vessel, data.assigned_to, current_user.id, data.notes, db
        )

        await db.commit()
        await db.refresh(activity, attribute_names=["comments"])
        activity.vessel_name = vessel.vessel_name  # type: ignore[attr-defined]
        activity.vessel_current_rob_mt = vessel.current_rob_mt  # type: ignore[attr-defined]
        return activity

    @staticmethod
    async def _assert_not_full_operation(activity: "VesselActivity", db: AsyncSession) -> None:
        """The old Start/Receipt/Bunkering/Discharge/Complete ROB-session flow
        is retired for full_operation — the Vessel BDN now carries the
        truck-vs-vessel reconciliation, and approving it is what updates ROB
        (see VesselBdnService.approve_vessel_bdn). Guarded here, not just
        removed from the UI: complete() is the one method that used to write
        the RobEntry directly, and leaving it silently reachable would let a
        stray call double-credit the vessel now that approval does it too."""
        op = await db.get(Operation, activity.operation_id)
        if op and op.type == OperationType.full_operation:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This flow has been retired for Full Operation — quantities and ROB "
                       "are now recorded on the Vessel BDN. Submit a Vessel BDN instead.",
            )

    @staticmethod
    async def start(activity_id: UUID, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_not_full_operation(activity, db)

        if (
            current_user.id != activity.assigned_to
            and current_user.role != UserRole.bunker_manager
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")

        if activity.status != VesselActivityStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Activity is already '{activity.status.value}'",
            )

        activity.status = VesselActivityStatus.active
        activity.started_at = datetime.utcnow()
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="START_VESSEL_ACTIVITY",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={"activity_number": activity.activity_number},
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def record_receipt(
        activity_id: UUID,
        data: VesselActivityRecordReceipt,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_not_full_operation(activity, db)
        VesselActivityService._assert_active(activity, current_user)

        activity.vessel_received_mt = data.vessel_received_mt
        activity.previous_rob_mt = data.previous_rob_mt

        if data.truck_delivered_mt is not None:
            activity.truck_delivered_mt = data.truck_delivered_mt
            activity.variance_mt = data.truck_delivered_mt - data.vessel_received_mt

        activity.new_rob_mt = data.previous_rob_mt + data.vessel_received_mt

        if data.product_type:
            activity.product_type = data.product_type
        if data.spillage_mt is not None:
            activity.spillage_mt = data.spillage_mt
        if data.temperature_celsius is not None:
            activity.temperature_celsius = data.temperature_celsius
        if data.density is not None:
            activity.density = data.density
        if data.notes:
            activity.notes = data.notes

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="RECORD_VESSEL_RECEIPT",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "vessel_received_mt": str(data.vessel_received_mt),
                "previous_rob_mt": str(data.previous_rob_mt),
                "new_rob_mt": str(activity.new_rob_mt),
                "variance_mt": str(activity.variance_mt) if activity.variance_mt is not None else None,
            },
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def record_bunkering(
        activity_id: UUID,
        data: VesselActivityRecordBunkering,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_not_full_operation(activity, db)
        VesselActivityService._assert_active(activity, current_user)

        if data.bunkering_start_at:
            activity.bunkering_start_at = data.bunkering_start_at
        if data.bunkering_end_at:
            activity.bunkering_end_at = data.bunkering_end_at
        if data.notes:
            activity.notes = data.notes

        await db.flush()
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def record_discharge(
        activity_id: UUID,
        data: VesselActivityRecordDischarge,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_not_full_operation(activity, db)
        VesselActivityService._assert_active(activity, current_user)

        activity.quantity_discharged_mt = data.quantity_discharged_mt
        if activity.new_rob_mt is not None:
            activity.final_rob_mt = activity.new_rob_mt - data.quantity_discharged_mt

        if data.discharge_start_at:
            activity.discharge_start_at = data.discharge_start_at
        if data.discharge_end_at:
            activity.discharge_end_at = data.discharge_end_at
        if data.notes:
            activity.notes = data.notes

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="RECORD_VESSEL_DISCHARGE",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "quantity_discharged_mt": str(data.quantity_discharged_mt),
                "final_rob_mt": str(activity.final_rob_mt),
            },
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def complete(
        activity_id: UUID,
        data: VesselActivityComplete,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_not_full_operation(activity, db)
        VesselActivityService._assert_active(activity, current_user)

        final_rob = activity.final_rob_mt if activity.final_rob_mt is not None else activity.new_rob_mt

        activity.status = VesselActivityStatus.completed
        activity.completed_at = datetime.utcnow()
        if data.completion_notes:
            activity.completion_notes = data.completion_notes
        if final_rob is not None:
            activity.final_rob_mt = final_rob

        # Update vessel's current ROB
        vessel = await db.get(Vessel, activity.vessel_id)
        if vessel and final_rob is not None:
            rob_before = vessel.current_rob_mt
            vessel.current_rob_mt = final_rob

            db.add(RobEntry(
                vessel_id=activity.vessel_id,
                operation_id=activity.operation_id,
                entry_type=RobEntryType.replenishment,
                quantity_mt=activity.vessel_received_mt,
                rob_before_mt=rob_before,
                rob_after_mt=final_rob,
                recorded_by=current_user.id,
                supervisor_id=activity.assigned_to,
                spillage_mt=activity.spillage_mt,
                temperature_celsius=activity.temperature_celsius,
                source_description=f"VesselActivity {activity.activity_number}",
                notes=data.completion_notes,
            ))

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="COMPLETE_VESSEL_ACTIVITY",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "activity_number": activity.activity_number,
                "final_rob_mt": str(final_rob) if final_rob is not None else None,
            },
        ))

        # Fetch operation for context in notifications
        op = await db.get(Operation, activity.operation_id)
        op_number = op.operation_number if op else str(activity.operation_id)
        vessel_name = vessel.vessel_name if vessel else str(activity.vessel_id)

        from app.services.notification_service import notify

        # Notify all Bunker Managers
        bm_result = await db.execute(
            select(User).where(User.role == UserRole.bunker_manager, User.is_active == True)
        )
        for bm in bm_result.scalars().all():
            await notify(
                db=db,
                user_id=bm.id,
                type_="vessel_activity_completed",
                title=f"Vessel Activity Completed — {activity.activity_number}",
                message=(
                    f"Marine Manager has completed vessel activity {activity.activity_number} "
                    f"for operation {op_number} aboard {vessel_name}. "
                    f"Final ROB: {float(final_rob):.3f} L. Ready for BDN and reconciliation."
                    if final_rob is not None else
                    f"Marine Manager has completed vessel activity {activity.activity_number} "
                    f"for operation {op_number} aboard {vessel_name}. Review and proceed."
                ),
                priority="high",
                operation_id=activity.operation_id,
                action_url=f"/operations/{activity.operation_id}",
                channels=["in_app", "whatsapp"],
                wa_template="operation_update",
                wa_kwargs={
                    "operation_number": op_number,
                    "status": f"Vessel activity {activity.activity_number} completed",
                },
            )

        # Notify all Finance Managers for reconciliation awareness
        fm_result = await db.execute(
            select(User).where(User.role == UserRole.finance_manager, User.is_active == True)
        )
        for fm in fm_result.scalars().all():
            await notify(
                db=db,
                user_id=fm.id,
                type_="vessel_activity_completed",
                title=f"Vessel Activity Complete — Finance Reconciliation Required",
                message=(
                    f"Vessel activity {activity.activity_number} for operation {op_number} "
                    f"is complete. Actual received: {float(activity.vessel_received_mt):.3f} L, "
                    f"Final ROB: {float(final_rob):.3f} L. Align with invoicing and expense tracking."
                    if final_rob is not None and activity.vessel_received_mt is not None else
                    f"Vessel activity {activity.activity_number} for operation {op_number} "
                    f"is complete. Please align with invoicing and expense tracking."
                ),
                priority="normal",
                operation_id=activity.operation_id,
                action_url=f"/operations/{activity.operation_id}",
                channels=["in_app"],
            )

        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def patch_initial_rob(
        activity_id: UUID,
        data: VesselActivityPatchInitialRob,
        current_user: User,
        db: AsyncSession,
    ) -> VesselActivity:
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Bunker Manager can edit Initial ROB")

        # Deliberately NOT blocked once the activity is completed — correcting
        # a figure after the fact is exactly what this is for. Every change is
        # reason-bearing and audit-logged.
        activity = await VesselActivityService._get_or_404(activity_id, db)

        old_value = str(activity.initial_rob_mt) if activity.initial_rob_mt is not None else None
        activity.initial_rob_mt = data.initial_rob_mt

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="EDIT_INITIAL_ROB",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "activity_number": activity.activity_number,
                "initial_rob_mt": {"from": old_value, "to": str(data.initial_rob_mt)},
            },
            reason=data.reason,
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def cancel(activity_id: UUID, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        if activity.status == VesselActivityStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot cancel a completed activity",
            )
        activity.status = VesselActivityStatus.cancelled
        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=activity.operation_id,
            action="CANCEL_VESSEL_ACTIVITY",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={"activity_number": activity.activity_number},
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def list_by_operation(operation_id: UUID, db: AsyncSession) -> List[VesselActivity]:
        result = await db.execute(
            select(VesselActivity)
            .execution_options(populate_existing=True)
            .where(VesselActivity.operation_id == operation_id)
            .options(
                selectinload(VesselActivity.vessel),
                selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder),
                selectinload(VesselActivity.updates).selectinload(VesselActivityUpdate.recorder),
                selectinload(VesselActivity.legs),
            )
            .order_by(VesselActivity.created_at.desc())
        )
        activities = list(result.scalars().all())
        for a in activities:
            _attach_vessel_name(a)
        return activities

    @staticmethod
    async def list_assigned_to(user_id: UUID, db: AsyncSession) -> List[VesselActivity]:
        result = await db.execute(
            select(VesselActivity)
            .execution_options(populate_existing=True)
            .where(VesselActivity.assigned_to == user_id)
            .options(
                selectinload(VesselActivity.vessel),
                selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder),
                selectinload(VesselActivity.updates).selectinload(VesselActivityUpdate.recorder),
                selectinload(VesselActivity.legs),
            )
            .order_by(VesselActivity.created_at.desc())
        )
        activities = list(result.scalars().all())
        for a in activities:
            _attach_vessel_name(a)
        return activities

    @staticmethod
    async def get(activity_id: UUID, db: AsyncSession) -> VesselActivity:
        return await VesselActivityService._get_or_404(activity_id, db)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_or_404(activity_id: UUID, db: AsyncSession) -> VesselActivity:
        result = await db.execute(
            select(VesselActivity)
            # populate_existing: without it, an activity already loaded
            # earlier in this session (e.g. at the start of the same method,
            # before a mutation) would be returned from the identity map with
            # its eagerly-loaded collections (comments) stale/pre-mutation.
            .execution_options(populate_existing=True)
            .where(VesselActivity.id == activity_id)
            .options(
                selectinload(VesselActivity.vessel),
                selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder),
                selectinload(VesselActivity.updates).selectinload(VesselActivityUpdate.recorder),
                selectinload(VesselActivity.legs),
            )
        )
        a = result.scalar_one_or_none()
        if not a:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel activity not found")
        _attach_vessel_name(a)
        return a

    @staticmethod
    def _assert_active(activity: VesselActivity, current_user: User) -> None:
        if (
            current_user.id != activity.assigned_to
            and current_user.role != UserRole.bunker_manager
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")
        if activity.status != VesselActivityStatus.active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Activity must be active (currently '{activity.status.value}'). Start it first.",
            )

    @staticmethod
    def _assert_authorized(activity: VesselActivity, current_user: User) -> None:
        """Stage/comment/HSE/discharge-quantity actions: the assigned Marine
        Manager, any Ops Supervisor, or Bunker Manager — broader than
        `_assert_active`'s single-assignee check (spec: "Marine / Ops
        Supervisor" progress stages), and deliberately not status-gated,
        since stages are the source of truth for progress, not `status`."""
        allowed_roles = (UserRole.ops_supervisor, UserRole.bunker_manager)
        if current_user.id != activity.assigned_to and current_user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")

    @staticmethod
    async def advance_stage(activity_id: UUID, data: AdvanceStageRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        # Sequence check — allows re-entry/correction of an already-logged
        # stage (fixing a wrong time doesn't regress overall progress), just
        # stops skipping ahead of the current position by more than one step.
        target_idx = _STAGE_ORDER.index(data.stage)
        current_idx = _STAGE_ORDER.index(activity.stage) if activity.stage else -1
        if target_idx > current_idx + 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot skip ahead to '{data.stage.value}' — current stage is "
                       f"'{activity.stage.value if activity.stage else 'not started'}'",
            )

        setattr(activity, f"stage_{data.stage.value}_at", data.occurred_at)
        if target_idx >= current_idx:
            activity.stage = data.stage

        if data.comment:
            db.add(VesselActivityComment(
                vessel_activity_id=activity.id, stage=data.stage,
                comment=data.comment, recorded_by=current_user.id, recorded_at=datetime.utcnow(),
            ))

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="ADVANCE_VESSEL_STAGE",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={"stage": data.stage.value, "occurred_at": data.occurred_at.isoformat()},
        ))

        # System-triggered: once every non-cancelled VesselActivity on the
        # operation has reached discharge_completed, the operation itself
        # auto-advances vessel_operations -> pending_completion.
        if data.stage == VesselStage.discharge_completed:
            await VesselActivityService._maybe_auto_complete_operation(
                activity.operation_id, current_user, db,
                is_activity_pending=or_(
                    VesselActivity.stage.is_(None),
                    VesselActivity.stage != VesselStage.discharge_completed,
                ),
                reason="All vessel runs discharge-completed",
            )

        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection (picking up the row just
        # added) but without cascading the nested .recorder eager-load, so
        # _attach_vessel_name's `c.recorder` access would otherwise lazy-load
        # and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def _maybe_auto_complete_operation(
        operation_id: UUID, current_user: User, db: AsyncSession, *,
        is_activity_pending, reason: str,
    ) -> None:
        """Shared by advance_stage (stage-based, full_operation) and
        complete_vessel_operation (vessel_only): once every non-cancelled
        VesselActivity on the operation has finished, auto-advance
        vessel_operations -> pending_completion. `is_activity_pending` is a
        SQLAlchemy boolean expression selecting rows that still count as
        "not done yet" — callers must use the NULL-safe `or_(col.is_(None),
        col != done_value)` form, never a plain `!=`, since SQL's
        three-valued logic silently excludes NULL rows from a plain `!=`
        and would trigger this prematurely."""
        op = await db.get(Operation, operation_id)
        if not op or op.status != OperationStatus.vessel_operations:
            return
        remaining_result = await db.execute(
            select(func.count()).select_from(VesselActivity).where(
                and_(
                    VesselActivity.operation_id == operation_id,
                    VesselActivity.status != VesselActivityStatus.cancelled,
                    is_activity_pending,
                )
            )
        )
        if (remaining_result.scalar() or 0) == 0:
            await _transition_operation(op, OperationStatus.pending_completion, current_user, db, reason=reason)

    @staticmethod
    async def add_comment(activity_id: UUID, data: AddCommentRequest, current_user: User, db: AsyncSession) -> VesselActivityComment:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        comment = VesselActivityComment(
            vessel_activity_id=activity.id, stage=data.stage,
            comment=data.comment, recorded_by=current_user.id, recorded_at=datetime.utcnow(),
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        comment.recorded_by_name = current_user.full_name  # type: ignore[attr-defined]
        return comment

    @staticmethod
    async def list_comments(activity_id: UUID, db: AsyncSession) -> List[VesselActivityComment]:
        await VesselActivityService._get_or_404(activity_id, db)
        result = await db.execute(
            select(VesselActivityComment)
            .options(selectinload(VesselActivityComment.recorder))
            .where(VesselActivityComment.vessel_activity_id == activity_id)
            .order_by(VesselActivityComment.recorded_at.asc())
        )
        comments = list(result.scalars().all())
        for c in comments:
            c.recorded_by_name = c.recorder.full_name if c.recorder else None  # type: ignore[attr-defined]
        return comments

    @staticmethod
    async def set_cast_off_contacts(
        activity_id: UUID, data: SetCastOffContactsRequest, current_user: User, db: AsyncSession
    ) -> VesselActivity:
        """Upsert the Cast Off client block — client name, their vessel's name,
        and the email recipients for this run.

        Deliberately ungated by stage or status. The BM asked to capture this at
        Cast Off, but a detail remembered at Alongside is still worth recording,
        and a wrong address found after completion still needs correcting.
        Nothing is sent from here: these recipients only ever reach an email
        once the BM has approved and then explicitly sent it.
        """
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        before = {
            "client_name": activity.cast_off_client_name,
            "client_vessel_name": activity.cast_off_client_vessel_name,
            "emails": list(activity.cast_off_client_emails or []),
        }

        activity.cast_off_client_name = data.client_name
        activity.cast_off_client_vessel_name = data.client_vessel_name
        activity.cast_off_client_emails = data.emails

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id,
            action="SET_CAST_OFF_CONTACTS",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={"before": before, "after": {
                "client_name": data.client_name,
                "client_vessel_name": data.client_vessel_name,
                "emails": data.emails,
            }},
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    def _hse_field(phase: str, suffix: str) -> str:
        """Column name for one field of one HSE phase.

        The three phases live in three column sets on VesselActivity, and "pre"
        is the ORIGINAL unprefixed set (kept that way by migration 059 so the
        checklists already recorded needed no data migration).

        Every phase-to-column lookup in this file goes through here, and the
        result is asserted against the mapped columns before use — writing a
        name that isn't a real column would NOT raise on a declarative model,
        it would just set a throwaway Python attribute and silently drop the
        data. That is precisely how the stage timestamps were lost between
        migrations 057 and 058; it does not get to happen twice.
        """
        name = f"hse_{suffix}" if phase == "pre" else f"hse_{phase}_{suffix}"
        if name not in VesselActivity.__table__.columns:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unknown HSE field '{name}' for phase '{phase}'",
            )
        return name

    @staticmethod
    async def record_hse(activity_id: UUID, data: RecordHseRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        """Non-blocking safety record — a failed item never blocks progress
        or any subsequent stage/BDN/completion action.

        Records one of three checks (pre / during / post), selected by
        data.phase. The three are wholly independent: recording the During
        check neither requires nor alters the Pre check, so a run whose Pre
        check was never filled in can still record the other two.
        """
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        phase = data.phase
        f = lambda suffix: VesselActivityService._hse_field(phase, suffix)  # noqa: E731

        # Overwriting an already-recorded checklist is a correction, not a
        # fresh record: BM-only, reason required, and the previous checklist
        # is preserved in the audit entry so the original is never lost.
        # Scoped per phase — correcting the During check must not be gated on
        # whether the Pre check exists.
        existing_result = getattr(activity, f("result"))
        is_correction = existing_result is not None
        prior = None
        if is_correction:
            if current_user.role != UserRole.bunker_manager:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct a recorded HSE checklist")
            if not data.reason:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct a recorded HSE checklist")
            conducted_at = getattr(activity, f("conducted_at"))
            prior = {
                "result": existing_result.value if existing_result else None,
                "checklist": getattr(activity, f("checklist")),
                "notes": getattr(activity, f("notes")),
                "conducted_at": conducted_at.isoformat() if conducted_at else None,
            }

        setattr(activity, f("checklist"), [item.model_dump() for item in data.checklist])
        setattr(activity, f("result"), data.result)
        setattr(activity, f("conducted_by"), current_user.id)
        setattr(activity, f("conducted_at"), datetime.utcnow())
        setattr(activity, f("notes"), data.notes)
        setattr(activity, f("safety_officer"), data.safety_officer)

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id,
            action="CORRECT_VESSEL_HSE" if is_correction else "RECORD_VESSEL_HSE",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={"phase": phase, "result": data.result.value, "items": len(data.checklist), "previous": prior},
            reason=data.reason,
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    # ── Vessel-only: commence -> updates -> complete -> quantities ─────────────
    # Fully separate from the stage flow above and the old ROB-session flow —
    # applies only when operation.type == vessel_only (enforced below); the
    # old start()/record_receipt()/record_bunkering()/record_discharge()/
    # complete() stay untouched and simply aren't called for these activities.

    @staticmethod
    async def _assert_vessel_only(activity: VesselActivity, db: AsyncSession) -> Operation:
        op = await db.get(Operation, activity.operation_id)
        if not op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        if op.type != OperationType.vessel_only:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This action only applies to vessel-only operations",
            )
        return op

    @staticmethod
    async def commence(activity_id: UUID, data: VesselActivityCommenceRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if activity.commence_system_at is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This vessel run has already been commenced")

        activity.commence_system_at = datetime.utcnow()
        activity.commence_user_at = data.commenced_user_at
        activity.commence_description = data.description
        activity.status = VesselActivityStatus.active

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="COMMENCE_VESSEL_OPERATION",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={
                "commence_system_at": activity.commence_system_at.isoformat(),
                "commence_user_at": data.commenced_user_at.isoformat(),
                "description": data.description,
            },
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def add_update(
        activity_id: UUID, content: str,
        image_bytes: Optional[bytes], image_filename: Optional[str], image_mime: Optional[str],
        current_user: User, db: AsyncSession,
    ) -> VesselActivityUpdate:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if activity.commence_system_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Commence the vessel operation before adding updates")

        image_url = None
        if image_bytes:
            import uuid as _uuid
            from app.services.document_service import _upload_to_supabase
            ext = image_filename.rsplit(".", 1)[-1] if image_filename and "." in image_filename else "jpg"
            storage_path = f"vessel-activity-updates/{activity_id}/{_uuid.uuid4()}.{ext}"
            image_url = await _upload_to_supabase(image_bytes, storage_path, image_mime or "application/octet-stream")

        update = VesselActivityUpdate(
            vessel_activity_id=activity.id, content=content, image_url=image_url,
            recorded_by=current_user.id, recorded_at=datetime.utcnow(),
        )
        db.add(update)
        await db.commit()
        await db.refresh(update)
        update.recorded_by_name = current_user.full_name  # type: ignore[attr-defined]
        return update

    @staticmethod
    async def list_updates(activity_id: UUID, db: AsyncSession) -> List[VesselActivityUpdate]:
        await VesselActivityService._get_or_404(activity_id, db)
        result = await db.execute(
            select(VesselActivityUpdate)
            .options(selectinload(VesselActivityUpdate.recorder))
            .where(VesselActivityUpdate.vessel_activity_id == activity_id)
            .order_by(VesselActivityUpdate.recorded_at.desc())
        )
        updates = list(result.scalars().all())
        for u in updates:
            u.recorded_by_name = u.recorder.full_name if u.recorder else None  # type: ignore[attr-defined]
        return updates

    @staticmethod
    async def complete_vessel_operation(activity_id: UUID, data: VesselActivityCompleteVesselOpRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if activity.commence_system_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Commence the vessel operation before completing it")
        if activity.complete_system_at is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This vessel run has already been completed")

        activity.complete_system_at = datetime.utcnow()
        activity.complete_user_at = data.completed_user_at
        activity.complete_description = data.description
        activity.status = VesselActivityStatus.completed

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="COMPLETE_VESSEL_OPERATION",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={
                "complete_system_at": activity.complete_system_at.isoformat(),
                "complete_user_at": data.completed_user_at.isoformat(),
                "description": data.description,
            },
        ))

        # NOTE: unlike the old single-run vessel-only flow, Loading Completed
        # no longer means the operation is done — that responsibility now
        # belongs entirely to _maybe_auto_complete_operation_from_legs, fired
        # once every receiving-vessel leg reaches Discharge Completed. An
        # operation can sit here indefinitely with Loading Completed but no
        # legs yet, or legs still in progress.

        op = await db.get(Operation, activity.operation_id)
        op_number = op.operation_number if op else str(activity.operation_id)
        from app.services.notification_service import notify
        bm_result = await db.execute(select(User).where(User.role == UserRole.bunker_manager, User.is_active == True))
        for bm in bm_result.scalars().all():
            await notify(
                db=db, user_id=bm.id, type_="vessel_activity_completed",
                title=f"Loading Completed — {activity.activity_number}",
                message=f"Loading completed for {activity.activity_number} on operation {op_number}. Add receiving vessels to begin delivery.",
                priority="high", operation_id=activity.operation_id, action_url=f"/operations/{activity.operation_id}",
                channels=["in_app", "whatsapp"], wa_template="operation_update",
                wa_kwargs={"operation_number": op_number, "status": f"Loading completed for {activity.activity_number}"},
            )

        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def record_quantities(activity_id: UUID, data: RecordVesselOperationQuantitiesRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if activity.complete_system_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Complete the vessel operation before recording quantities")

        is_correction = activity.discharged_quantity_litres is not None
        if is_correction:
            if current_user.role != UserRole.bunker_manager:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct recorded quantities")
            if not data.reason:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct recorded quantities")

        vessel = await db.get(Vessel, activity.vessel_id)
        rob_source = f"VesselActivity {activity.activity_number}{_QUANTITIES_ROB_SOURCE_SUFFIX}"

        # On correction, reverse the previous pair's net effect before
        # applying the new one, so the ROB ledger never double-counts.
        if is_correction and vessel:
            prev_result = await db.execute(
                select(RobEntry).where(
                    and_(RobEntry.vessel_id == activity.vessel_id, RobEntry.source_description == rob_source)
                )
            )
            prev_entries = list(prev_result.scalars().all())
            reversed_net = sum((e.quantity_mt for e in prev_entries), Decimal("0"))
            vessel.current_rob_mt = (vessel.current_rob_mt or Decimal("0")) - reversed_net
            for e in prev_entries:
                await db.delete(e)
            await db.flush()

        activity.discharged_quantity_litres = data.discharged_quantity_litres
        activity.received_quantity_litres = data.received_quantity_litres
        activity.density = data.density
        activity.temperature_celsius = data.temperature_celsius
        activity.vcf = data.vcf
        activity.gov = data.gov
        activity.gsv = data.gov * data.vcf
        activity.mt_vacuum = activity.gsv * data.density
        activity.quantity_recorded_at = datetime.utcnow()
        activity.quantity_description = data.description

        # ROB update — Received adds, Discharged subtracts. Discharged uses
        # the VCF/density-corrected MTvac figure (the custody-transfer-grade
        # reading); Received uses a simpler direct litres*density conversion
        # (an operational receipt log figure, not a certified reading).
        discharged_mt = activity.mt_vacuum
        received_mt = data.received_quantity_litres * data.density
        if vessel:
            rob_before = vessel.current_rob_mt or Decimal("0")
            rob_after_receipt = rob_before + received_mt
            db.add(RobEntry(
                vessel_id=activity.vessel_id, operation_id=activity.operation_id,
                entry_type=RobEntryType.replenishment, quantity_mt=received_mt,
                rob_before_mt=rob_before, rob_after_mt=rob_after_receipt,
                recorded_by=current_user.id, supervisor_id=activity.assigned_to,
                source_description=rob_source, notes=data.description,
            ))
            rob_after_discharge = rob_after_receipt - discharged_mt
            db.add(RobEntry(
                vessel_id=activity.vessel_id, operation_id=activity.operation_id,
                entry_type=RobEntryType.discharge, quantity_mt=-discharged_mt,
                rob_before_mt=rob_after_receipt, rob_after_mt=rob_after_discharge,
                recorded_by=current_user.id, supervisor_id=activity.assigned_to,
                source_description=rob_source, notes=data.description,
            ))
            vessel.current_rob_mt = rob_after_discharge

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="RECORD_VESSEL_OPERATION_QUANTITIES",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={
                "discharged_quantity_litres": str(data.discharged_quantity_litres),
                "received_quantity_litres": str(data.received_quantity_litres),
                "mt_vacuum": str(activity.mt_vacuum),
            },
            reason=data.reason,
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def correct_timing(activity_id: UUID, data: VesselActivityCorrectTimingRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct vessel-operation timings")

        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        changes = {}
        for field in ("commence_system_at", "commence_user_at", "complete_system_at", "complete_user_at"):
            new_value = getattr(data, field)
            if new_value is not None:
                old_value = getattr(activity, field)
                changes[field] = {"from": old_value.isoformat() if old_value else None, "to": new_value.isoformat()}
                setattr(activity, field, new_value)
        # Free-text on the same correction call — an empty string clears the
        # field, which `is not None` allows deliberately (unlike the timings,
        # where clearing a recorded instant makes no sense).
        for field in ("commence_description", "complete_description", "notes"):
            new_value = getattr(data, field)
            if new_value is not None:
                old_value = getattr(activity, field)
                changes[field] = {"from": old_value, "to": new_value}
                setattr(activity, field, new_value or None)

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="CORRECT_VESSEL_OPERATION_TIMING",
            entity_type="vessel_activity", entity_id=activity.id,
            changes=changes, reason=data.reason,
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    @staticmethod
    async def record_discharge_quantities(activity_id: UUID, data: RecordDischargeQuantitiesRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        """Supplements record_discharge — the system calculates GSV/MTvac
        from the submitted readings; nobody does this arithmetic by hand."""
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        activity.gov = data.gov
        activity.vcf = data.vcf
        activity.gsv = data.gov * data.vcf
        activity.mt_vacuum = activity.gsv * data.density
        activity.density = data.density

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="RECORD_VESSEL_DISCHARGE_QUANTITIES",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={"gov": str(data.gov), "vcf": str(data.vcf), "gsv": str(activity.gsv), "mt_vacuum": str(activity.mt_vacuum)},
        ))
        await db.commit()
        # Re-fetch through _get_or_404 rather than a bare refresh() — a plain
        # refresh reloads the comments collection but without cascading the
        # nested .recorder eager-load, so _attach_vessel_name's `c.recorder`
        # access would otherwise lazy-load and crash in this async context.
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    # ── Vessel-only: six-stage journey + multiple receiving-vessel legs ────
    # Loading Commenced/Completed reuse commence()/complete_vessel_operation()
    # above unchanged (loading happens once, same cardinality as
    # commence/complete always had). Delivery repeats per receiving vessel —
    # each leg below is fully independent: own 4-stage tracker, own HSE, own
    # discharge readings, own BDN. The BM can add a leg at any point,
    # including after other legs on the same activity already finished.

    @staticmethod
    async def _get_leg_or_404(leg_id: UUID, db: AsyncSession) -> VesselActivityLeg:
        result = await db.execute(
            select(VesselActivityLeg)
            .execution_options(populate_existing=True)
            .where(VesselActivityLeg.id == leg_id)
        )
        leg = result.scalar_one_or_none()
        if not leg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiving-vessel leg not found")
        return leg

    @staticmethod
    async def add_leg(activity_id: UUID, data: VesselActivityLegCreate, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        # BM-only — the master spec names the BM specifically for this
        # action, unlike commence/updates/complete/quantities which say
        # "MM or OS." No gate on Loading Completed: registering a receiving
        # vessel is bookkeeping, not a physical stage.
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can add a receiving vessel")

        leg = VesselActivityLeg(
            vessel_activity_id=activity.id,
            receiving_vessel_name=data.receiving_vessel_name,
            imo_number=data.imo_number,
            eta_at=data.eta_at,
            created_by=current_user.id,
        )
        db.add(leg)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="ADD_VESSEL_ACTIVITY_LEG",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={"receiving_vessel_name": data.receiving_vessel_name, "imo_number": data.imo_number},
        ))

        # If a BM adds a receiving vessel after the operation would
        # otherwise have already auto-completed on the legs that existed
        # before it, pull the operation back into vessel_operations — same
        # "keep status honest" precedent as VesselActivityService.create()
        # already applies when a BM adds another vessel activity late.
        op = await db.get(Operation, activity.operation_id)
        if op and op.status in (OperationStatus.pending_completion, OperationStatus.bdn_pending):
            await _transition_operation(op, OperationStatus.vessel_operations, current_user, db, reason="New receiving vessel added")

        await db.commit()
        await db.refresh(leg)
        return leg

    @staticmethod
    async def list_legs(activity_id: UUID, db: AsyncSession) -> List[VesselActivityLeg]:
        await VesselActivityService._get_or_404(activity_id, db)
        result = await db.execute(
            select(VesselActivityLeg)
            .where(VesselActivityLeg.vessel_activity_id == activity_id)
            .order_by(VesselActivityLeg.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def advance_leg_stage(leg_id: UUID, data: AdvanceLegStageRequest, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if leg.cancelled_at is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This receiving vessel has been cancelled")

        target_idx = _LEG_STAGE_ORDER.index(data.stage)
        current_idx = _LEG_STAGE_ORDER.index(leg.stage) if leg.stage else -1
        if target_idx > current_idx + 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot skip ahead to '{data.stage.value}' — current stage is "
                       f"'{leg.stage.value if leg.stage else 'not started'}'",
            )
        if data.stage == VesselLegStage.cast_off and leg.stage is None and activity.complete_system_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Loading must be completed before a receiving vessel can cast off",
            )

        now = datetime.utcnow()
        setattr(leg, f"stage_{data.stage.value}_system_at", now)
        setattr(leg, f"stage_{data.stage.value}_user_at", data.occurred_at)
        if target_idx >= current_idx:
            leg.stage = data.stage

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="ADVANCE_VESSEL_ACTIVITY_LEG_STAGE",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={
                "stage": data.stage.value,
                f"stage_{data.stage.value}_system_at": now.isoformat(),
                f"stage_{data.stage.value}_user_at": data.occurred_at.isoformat(),
            },
        ))

        if data.stage == VesselLegStage.discharge_completed:
            await VesselActivityService._maybe_auto_complete_operation_from_legs(activity.operation_id, current_user, db)

        await db.commit()
        leg = await VesselActivityService._get_leg_or_404(leg.id, db)
        return leg

    @staticmethod
    async def _maybe_auto_complete_operation_from_legs(operation_id: UUID, current_user: User, db: AsyncSession) -> None:
        """Once every non-cancelled receiving-vessel leg (across every
        non-cancelled VesselActivity on the operation) has reached
        Discharge Completed, auto-advance vessel_operations ->
        pending_completion. An operation with Loading Completed but zero
        legs must never look done — guarded explicitly below."""
        op = await db.get(Operation, operation_id)
        # Re-derives in BOTH directions. A correction can un-finish an
        # operation (a leg rolled back, a cancelled leg restored) just as
        # easily as finish it, so a one-way "advance when everything is done"
        # check would strand the operation at pending_completion after any
        # rollback. bdn_approved/completed are deliberately excluded — once a
        # BDN is approved the gate is closed by an explicit decision, not by
        # leg state.
        if not op or op.status not in (
            OperationStatus.vessel_operations,
            OperationStatus.pending_completion,
            OperationStatus.bdn_pending,
        ):
            return

        total_result = await db.execute(
            select(func.count()).select_from(VesselActivityLeg).join(
                VesselActivity, VesselActivityLeg.vessel_activity_id == VesselActivity.id
            ).where(
                and_(
                    VesselActivity.operation_id == operation_id,
                    VesselActivity.status != VesselActivityStatus.cancelled,
                    VesselActivityLeg.cancelled_at.is_(None),
                )
            )
        )
        if (total_result.scalar() or 0) == 0:
            return

        remaining_result = await db.execute(
            select(func.count()).select_from(VesselActivityLeg).join(
                VesselActivity, VesselActivityLeg.vessel_activity_id == VesselActivity.id
            ).where(
                and_(
                    VesselActivity.operation_id == operation_id,
                    VesselActivity.status != VesselActivityStatus.cancelled,
                    VesselActivityLeg.cancelled_at.is_(None),
                    or_(
                        VesselActivityLeg.stage.is_(None),
                        VesselActivityLeg.stage != VesselLegStage.discharge_completed,
                    ),
                )
            )
        )
        remaining = remaining_result.scalar() or 0
        if remaining == 0 and op.status == OperationStatus.vessel_operations:
            await _transition_operation(op, OperationStatus.pending_completion, current_user, db, reason="All receiving-vessel legs discharge-completed")
        elif remaining > 0 and op.status in (OperationStatus.pending_completion, OperationStatus.bdn_pending):
            await _transition_operation(op, OperationStatus.vessel_operations, current_user, db, reason="A receiving vessel is no longer discharge-completed")

    @staticmethod
    async def record_leg_hse(leg_id: UUID, data: RecordLegHseRequest, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        """Non-blocking safety record for one receiving-vessel leg — a
        failed item never blocks progress or any subsequent action."""
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        is_correction = leg.hse_result is not None
        prior = None
        if is_correction:
            if current_user.role != UserRole.bunker_manager:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct a recorded HSE checklist")
            if not data.reason:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct a recorded HSE checklist")
            prior = {
                "result": leg.hse_result.value if leg.hse_result else None,
                "checklist": leg.hse_checklist,
                "notes": leg.hse_notes,
                "conducted_at": leg.hse_conducted_at.isoformat() if leg.hse_conducted_at else None,
            }

        leg.hse_checklist = [item.model_dump() for item in data.checklist]
        leg.hse_result = data.result
        leg.hse_conducted_by = current_user.id
        leg.hse_conducted_at = datetime.utcnow()
        leg.hse_notes = data.notes
        leg.hse_safety_officer = data.safety_officer

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id,
            action="CORRECT_VESSEL_ACTIVITY_LEG_HSE" if is_correction else "RECORD_VESSEL_ACTIVITY_LEG_HSE",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={"result": data.result.value, "items": len(data.checklist), "previous": prior},
            reason=data.reason,
        ))
        await db.commit()
        leg = await VesselActivityService._get_leg_or_404(leg.id, db)
        return leg

    @staticmethod
    async def record_leg_quantities(leg_id: UUID, data: RecordLegQuantitiesRequest, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if leg.stage != VesselLegStage.discharge_completed:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Discharge must be completed on this receiving vessel before recording quantities")

        is_correction = leg.quantity_discharged_litres is not None
        if is_correction:
            if current_user.role != UserRole.bunker_manager:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct recorded quantities")
            if not data.reason:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct recorded quantities")

        vessel = await db.get(Vessel, activity.vessel_id)
        rob_source = f"VesselActivityLeg {leg.id}{_LEG_QUANTITIES_ROB_SOURCE_SUFFIX}"

        # On correction, reverse the previous entry's net effect before
        # applying the new one, so the ROB ledger never double-counts.
        if is_correction and vessel:
            prev_result = await db.execute(
                select(RobEntry).where(
                    and_(RobEntry.vessel_id == activity.vessel_id, RobEntry.source_description == rob_source)
                )
            )
            prev_entries = list(prev_result.scalars().all())
            reversed_net = sum((e.quantity_mt for e in prev_entries), Decimal("0"))
            vessel.current_rob_mt = (vessel.current_rob_mt or Decimal("0")) - reversed_net
            for e in prev_entries:
                await db.delete(e)
            await db.flush()

        leg.quantity_discharged_litres = data.quantity_discharged_litres
        leg.density = data.density
        leg.temperature = data.temperature
        leg.vcf = data.vcf
        leg.gov = data.gov
        leg.gsv = data.gov * data.vcf
        leg.mt_vacuum = leg.gsv * data.density
        leg.quantity_recorded_at = datetime.utcnow()
        leg.quantity_description = data.description

        # Discharge-only — this leg has no "received" concept (that's the
        # loading-level record_loading_receipt below). Uses the
        # VCF/density-corrected MTvac figure, matching the custody-transfer
        # convention used everywhere else this arithmetic appears.
        discharged_mt = leg.mt_vacuum
        if vessel:
            rob_before = vessel.current_rob_mt or Decimal("0")
            rob_after = rob_before - discharged_mt
            db.add(RobEntry(
                vessel_id=activity.vessel_id, operation_id=activity.operation_id,
                entry_type=RobEntryType.discharge, quantity_mt=-discharged_mt,
                rob_before_mt=rob_before, rob_after_mt=rob_after,
                recorded_by=current_user.id, supervisor_id=activity.assigned_to,
                source_description=rob_source, notes=data.description,
            ))
            vessel.current_rob_mt = rob_after

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="RECORD_VESSEL_ACTIVITY_LEG_QUANTITIES",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={
                "quantity_discharged_litres": str(data.quantity_discharged_litres),
                "mt_vacuum": str(leg.mt_vacuum),
            },
            reason=data.reason,
        ))
        await db.commit()
        leg = await VesselActivityService._get_leg_or_404(leg.id, db)
        return leg

    @staticmethod
    async def correct_leg_timing(leg_id: UUID, data: CorrectLegTimingRequest, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct a leg timing")

        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        changes = {}
        for field in (
            "stage_cast_off_system_at", "stage_cast_off_user_at",
            "stage_alongside_system_at", "stage_alongside_user_at",
            "stage_discharge_commenced_system_at", "stage_discharge_commenced_user_at",
            "stage_discharge_completed_system_at", "stage_discharge_completed_user_at",
        ):
            new_value = getattr(data, field)
            if new_value is not None:
                old_value = getattr(leg, field)
                changes[field] = {"from": old_value.isoformat() if old_value else None, "to": new_value.isoformat()}
                setattr(leg, field, new_value)

        # Stage rollback — for a stage logged in error. Guarded, because the
        # leg's stage is what the operation's completion gate counts on.
        if data.stage is not None and data.stage != leg.stage:
            if leg.stage == VesselLegStage.discharge_completed:
                existing = await db.execute(
                    select(func.count()).select_from(BDN).where(
                        and_(BDN.vessel_leg_id == leg.id, BDN.status.in_([BdnStatus.pending, BdnStatus.approved]))
                    )
                )
                if (existing.scalar() or 0) > 0:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Cannot roll this receiving vessel back — a Vessel BDN has already been submitted for it. "
                               "Reject that BDN first.",
                    )
            changes["stage"] = {"from": leg.stage.value if leg.stage else None, "to": data.stage.value}
            leg.stage = data.stage

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="CORRECT_VESSEL_ACTIVITY_LEG_TIMING",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes=changes, reason=data.reason,
        ))
        # A rollback can un-complete the operation; a forward correction can
        # complete it. Re-evaluate either way.
        await VesselActivityService._maybe_auto_complete_operation_from_legs(activity.operation_id, current_user, db)

        await db.commit()
        leg = await VesselActivityService._get_leg_or_404(leg.id, db)
        return leg

    @staticmethod
    async def cancel_leg(leg_id: UUID, data: CancelLegRequest, current_user: User, db: AsyncSession) -> VesselActivityLeg:
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can cancel a receiving vessel")

        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        if leg.cancelled_at is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This receiving vessel is already cancelled")

        leg.cancelled_at = datetime.utcnow()
        leg.cancelled_reason = data.reason

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="CANCEL_VESSEL_ACTIVITY_LEG",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={"receiving_vessel_name": leg.receiving_vessel_name}, reason=data.reason,
        ))

        # Cancelling a leg can itself complete the remaining set — re-check.
        await VesselActivityService._maybe_auto_complete_operation_from_legs(activity.operation_id, current_user, db)

        await db.commit()
        leg = await VesselActivityService._get_leg_or_404(leg.id, db)
        return leg

    @staticmethod
    async def record_loading_receipt(activity_id: UUID, data: RecordLoadingReceiptRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        """One-time Received Quantity + quality readings at the loading
        step. Loading happens once per barge run — discharge readings are
        recorded independently per receiving-vessel leg (record_leg_quantities)."""
        activity = await VesselActivityService._get_or_404(activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)
        VesselActivityService._assert_authorized(activity, current_user)

        if activity.complete_system_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Complete Loading before recording the received quantity")

        is_correction = activity.loading_received_quantity_litres is not None
        if is_correction:
            if current_user.role != UserRole.bunker_manager:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the Bunker Manager can correct recorded quantities")
            if not data.reason:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A reason is required to correct recorded quantities")

        vessel = await db.get(Vessel, activity.vessel_id)
        rob_source = f"VesselActivity {activity.activity_number}{_LOADING_RECEIPT_ROB_SOURCE_SUFFIX}"

        if is_correction and vessel:
            prev_result = await db.execute(
                select(RobEntry).where(
                    and_(RobEntry.vessel_id == activity.vessel_id, RobEntry.source_description == rob_source)
                )
            )
            prev_entries = list(prev_result.scalars().all())
            reversed_net = sum((e.quantity_mt for e in prev_entries), Decimal("0"))
            vessel.current_rob_mt = (vessel.current_rob_mt or Decimal("0")) - reversed_net
            for e in prev_entries:
                await db.delete(e)
            await db.flush()

        activity.loading_received_quantity_litres = data.received_quantity_litres
        activity.loading_density = data.density
        activity.loading_temperature = data.temperature
        activity.loading_vcf = data.vcf
        activity.loading_gov = data.gov
        activity.loading_gsv = data.gov * data.vcf
        activity.loading_mt_vacuum = activity.loading_gsv * data.density
        activity.loading_quantity_recorded_at = datetime.utcnow()
        activity.loading_quantity_description = data.description

        # Receipt-only — adds to ROB. Uses the VCF/density-corrected MTvac
        # figure, same convention as the leg discharge side, since loading
        # now captures the same full reading set (GOV/VCF/density) too.
        received_mt = activity.loading_mt_vacuum
        if vessel:
            rob_before = vessel.current_rob_mt or Decimal("0")
            rob_after = rob_before + received_mt
            db.add(RobEntry(
                vessel_id=activity.vessel_id, operation_id=activity.operation_id,
                entry_type=RobEntryType.replenishment, quantity_mt=received_mt,
                rob_before_mt=rob_before, rob_after_mt=rob_after,
                recorded_by=current_user.id, supervisor_id=activity.assigned_to,
                source_description=rob_source, notes=data.description,
            ))
            vessel.current_rob_mt = rob_after

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="RECORD_LOADING_RECEIPT",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={
                "loading_received_quantity_litres": str(data.received_quantity_litres),
                "loading_mt_vacuum": str(activity.loading_mt_vacuum),
            },
            reason=data.reason,
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity

    # ── BM corrections — the Bunker Manager can fix any recorded detail ────
    # All BM-only, reason-required and audit-logged. Nothing is ever deleted:
    # a correction edits the record and marks it as edited. Evidence records
    # (RobEntry, VesselEta, ClientNotificationLog) are never touched by any of
    # this — those are corrected by appending, never by rewriting.

    @staticmethod
    def _assert_bm(current_user: User, what: str) -> None:
        if current_user.role != UserRole.bunker_manager:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Only the Bunker Manager can correct {what}",
            )

    @staticmethod
    async def edit_update(
        update_id: UUID, content: Optional[str], reason: str,
        image_bytes: Optional[bytes], image_filename: Optional[str], image_mime: Optional[str],
        current_user: User, db: AsyncSession,
    ) -> VesselActivityUpdate:
        VesselActivityService._assert_bm(current_user, "a posted update")
        result = await db.execute(
            select(VesselActivityUpdate)
            .options(selectinload(VesselActivityUpdate.recorder), selectinload(VesselActivityUpdate.editor))
            .where(VesselActivityUpdate.id == update_id)
        )
        update = result.scalar_one_or_none()
        if not update:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Update not found")

        activity = await VesselActivityService._get_or_404(update.vessel_activity_id, db)

        changes = {}
        if content is not None and content != update.content:
            changes["content"] = {"from": update.content, "to": content}
            update.content = content

        if image_bytes:
            import uuid as _uuid
            from app.services.document_service import _upload_to_supabase
            ext = image_filename.rsplit(".", 1)[-1] if image_filename and "." in image_filename else "jpg"
            storage_path = f"vessel-activity-updates/{update.vessel_activity_id}/{_uuid.uuid4()}.{ext}"
            new_url = await _upload_to_supabase(image_bytes, storage_path, image_mime or "application/octet-stream")
            changes["image_url"] = {"from": update.image_url, "to": new_url}
            update.image_url = new_url

        update.edited_at = datetime.utcnow()
        update.edited_by = current_user.id
        update.edit_reason = reason

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="EDIT_VESSEL_ACTIVITY_UPDATE",
            entity_type="vessel_activity_update", entity_id=update.id,
            changes=changes, reason=reason,
        ))
        await db.commit()

        result = await db.execute(
            select(VesselActivityUpdate)
            .options(selectinload(VesselActivityUpdate.recorder), selectinload(VesselActivityUpdate.editor))
            .where(VesselActivityUpdate.id == update_id)
        )
        update = result.scalar_one()
        update.recorded_by_name = update.recorder.full_name if update.recorder else None  # type: ignore[attr-defined]
        update.edited_by_name = update.editor.full_name if update.editor else None  # type: ignore[attr-defined]
        return update

    @staticmethod
    async def edit_comment(
        comment_id: UUID, data: EditVesselActivityCommentRequest, current_user: User, db: AsyncSession,
    ) -> VesselActivityComment:
        VesselActivityService._assert_bm(current_user, "a posted comment")
        result = await db.execute(
            select(VesselActivityComment)
            .options(selectinload(VesselActivityComment.recorder), selectinload(VesselActivityComment.editor))
            .where(VesselActivityComment.id == comment_id)
        )
        comment = result.scalar_one_or_none()
        if not comment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

        activity = await VesselActivityService._get_or_404(comment.vessel_activity_id, db)

        changes = {"comment": {"from": comment.comment, "to": data.comment}}
        comment.comment = data.comment
        comment.edited_at = datetime.utcnow()
        comment.edited_by = current_user.id
        comment.edit_reason = data.reason

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="EDIT_VESSEL_ACTIVITY_COMMENT",
            entity_type="vessel_activity_comment", entity_id=comment.id,
            changes=changes, reason=data.reason,
        ))
        await db.commit()

        result = await db.execute(
            select(VesselActivityComment)
            .options(selectinload(VesselActivityComment.recorder), selectinload(VesselActivityComment.editor))
            .where(VesselActivityComment.id == comment_id)
        )
        comment = result.scalar_one()
        comment.recorded_by_name = comment.recorder.full_name if comment.recorder else None  # type: ignore[attr-defined]
        comment.edited_by_name = comment.editor.full_name if comment.editor else None  # type: ignore[attr-defined]
        return comment

    @staticmethod
    async def edit_leg(
        leg_id: UUID, data: EditVesselActivityLegRequest, current_user: User, db: AsyncSession,
    ) -> VesselActivityLeg:
        VesselActivityService._assert_bm(current_user, "a receiving vessel")
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        changes = capture_diff(leg, update_data)

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="EDIT_VESSEL_ACTIVITY_LEG",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes=changes, reason=data.reason,
        ))
        await db.commit()
        return await VesselActivityService._get_leg_or_404(leg.id, db)

    @staticmethod
    async def set_leg_adhoc_client(
        leg_id: UUID, data: SetLegAdhocClientRequest, current_user: User, db: AsyncSession,
    ) -> VesselActivityLeg:
        """Capture-only ad-hoc client contact (decision 6) — for a receiving
        vessel with no registered client account. No send action wired to
        this in this build. Settable only once the leg has reached cast_off,
        matching the JourneyStepper's own gate for that leg."""
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        if leg.stage is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This receiving vessel hasn't reached Cast Off yet",
            )

        leg.adhoc_client_email = data.adhoc_client_email
        leg.adhoc_client_name = data.adhoc_client_name

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="SET_LEG_ADHOC_CLIENT",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes={"adhoc_client_email": data.adhoc_client_email, "adhoc_client_name": data.adhoc_client_name},
        ))
        await db.commit()
        return await VesselActivityService._get_leg_or_404(leg.id, db)

    @staticmethod
    async def uncancel_leg(
        leg_id: UUID, data: UncancelRequest, current_user: User, db: AsyncSession,
    ) -> VesselActivityLeg:
        VesselActivityService._assert_bm(current_user, "a cancelled receiving vessel")
        leg = await VesselActivityService._get_leg_or_404(leg_id, db)
        activity = await VesselActivityService._get_or_404(leg.vessel_activity_id, db)
        await VesselActivityService._assert_vessel_only(activity, db)

        if leg.cancelled_at is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This receiving vessel is not cancelled")

        changes = {
            "cancelled_at": {"from": leg.cancelled_at.isoformat(), "to": None},
            "cancelled_reason": {"from": leg.cancelled_reason, "to": None},
        }
        leg.cancelled_at = None
        leg.cancelled_reason = None

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="UNCANCEL_VESSEL_ACTIVITY_LEG",
            entity_type="vessel_activity_leg", entity_id=leg.id,
            changes=changes, reason=data.reason,
        ))

        # Restoring a leg that has not finished re-opens the completion gate;
        # restoring an already-finished one leaves it closed. The shared
        # re-derivation handles both directions.
        await VesselActivityService._maybe_auto_complete_operation_from_legs(activity.operation_id, current_user, db)

        await db.commit()
        return await VesselActivityService._get_leg_or_404(leg.id, db)

    @staticmethod
    async def uncancel(
        activity_id: UUID, data: UncancelRequest, current_user: User, db: AsyncSession,
    ) -> VesselActivity:
        VesselActivityService._assert_bm(current_user, "a cancelled vessel activity")
        activity = await VesselActivityService._get_or_404(activity_id, db)
        if activity.status != VesselActivityStatus.cancelled:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="This vessel activity is not cancelled")

        # Restore to whichever point it had actually reached rather than
        # blindly to `pending` — completing, cancelling, then restoring must
        # not silently rewind real progress.
        restored = (
            VesselActivityStatus.completed if activity.complete_system_at
            else VesselActivityStatus.active if activity.commence_system_at
            else VesselActivityStatus.pending
        )
        changes = {"status": {"from": VesselActivityStatus.cancelled.value, "to": restored.value}}
        activity.status = restored

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="UNCANCEL_VESSEL_ACTIVITY",
            entity_type="vessel_activity", entity_id=activity.id,
            changes=changes, reason=data.reason,
        ))
        await db.commit()
        activity = await VesselActivityService._get_or_404(activity.id, db)
        _attach_vessel_name(activity)
        return activity
