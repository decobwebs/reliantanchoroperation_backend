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

from app.models.bdn import VesselActivity, RobEntry, VesselActivityComment
from app.models.vessel import Vessel
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import VesselActivityStatus, RobEntryType, UserRole, VesselStage, OperationStatus
from app.schemas.vessel_activity import (
    VesselActivityCreate,
    VesselActivityRecordReceipt,
    VesselActivityRecordBunkering,
    VesselActivityRecordDischarge,
    VesselActivityComplete,
    VesselActivityPatchInitialRob,
    AdvanceStageRequest,
    AddCommentRequest,
    RecordHseRequest,
    RecordDischargeQuantitiesRequest,
)
from app.services.state_machine import StateMachine, StateMachineError
from app.utils.number_generator import generate_vessel_activity_number

# Stage order — used to validate forward progression (BM may still correct
# an already-logged stage; this only stops skipping ahead by mistake).
_STAGE_ORDER = [
    VesselStage.cast_off, VesselStage.outbound, VesselStage.alongside,
    VesselStage.hse_check, VesselStage.discharging, VesselStage.discharge_completed,
]


def _attach_vessel_name(activity: VesselActivity) -> None:
    """Attach non-mapped vessel/comment-author fields onto the activity for
    schema serialisation. Requires `.vessel` and `.comments.recorder` to
    already be eager-loaded on `activity` (see _get_or_404/list_* queries) —
    accessing them here otherwise triggers a lazy-load that fails in this
    async context."""
    vessel = getattr(activity, 'vessel', None)
    activity.vessel_name = vessel.vessel_name if vessel else None  # type: ignore[attr-defined]
    activity.vessel_current_rob_mt = vessel.current_rob_mt if vessel else None  # type: ignore[attr-defined]
    for c in getattr(activity, 'comments', None) or []:
        c.recorded_by_name = c.recorder.full_name if getattr(c, 'recorder', None) else None  # type: ignore[attr-defined]


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
            operation.type, operation.status, to_status, current_user.acting_as_role or current_user.role
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
        if not assignee or assignee.role != UserRole.marine_manager:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assigned user must be a Marine Manager",
            )

        # BM adding another vessel after others already finished — keep the
        # operation's own status honest without a manual fixup step.
        if op.status in (OperationStatus.pending_completion, OperationStatus.bdn_pending):
            await _transition_operation(op, OperationStatus.vessel_operations, current_user, db, reason="New vessel activity added")

        activity_number = await generate_vessel_activity_number(db)
        activity = VesselActivity(
            activity_number=activity_number,
            operation_id=operation_id,
            vessel_id=data.vessel_id,
            assigned_to=data.assigned_to,
            assigned_by=current_user.id,
            notes=data.notes,
            status=VesselActivityStatus.pending,
            initial_rob_mt=vessel.current_rob_mt,
        )
        db.add(activity)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_VESSEL_ACTIVITY",
            entity_type="vessel_activity",
            entity_id=activity.id,
            changes={
                "activity_number": activity_number,
                "vessel_id": str(data.vessel_id),
                "assigned_to": str(data.assigned_to),
            },
        ))

        # Notify the assigned Marine Manager
        from app.services.notification_service import notify
        await notify(
            db=db,
            user_id=data.assigned_to,
            type_="vessel_activity_assigned",
            title=f"Vessel Activity Assigned — {activity_number}",
            message=(
                f"You have been assigned to oversee vessel bunkering/discharge for "
                f"operation {op.operation_number} aboard {vessel.vessel_name}. "
                f"Please initiate the activity and begin recording."
            ),
            priority="high",
            operation_id=operation_id,
            action_url=f"/operations/{operation_id}",
            channels=["in_app", "whatsapp"],
            wa_template="task_assigned",
            wa_kwargs={
                "operation_number": op.operation_number,
                "task": f"Vessel activity {activity_number} — {vessel.vessel_name}",
            },
        )

        await db.commit()
        await db.refresh(activity, attribute_names=["comments"])
        activity.vessel_name = vessel.vessel_name  # type: ignore[attr-defined]
        activity.vessel_current_rob_mt = vessel.current_rob_mt  # type: ignore[attr-defined]
        return activity

    @staticmethod
    async def start(activity_id: UUID, current_user: User, db: AsyncSession) -> VesselActivity:
        activity = await VesselActivityService._get_or_404(activity_id, db)

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

        activity = await VesselActivityService._get_or_404(activity_id, db)

        if activity.status == VesselActivityStatus.completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot edit Initial ROB on a completed activity",
            )

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
            .options(selectinload(VesselActivity.vessel), selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder))
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
            .options(selectinload(VesselActivity.vessel), selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder))
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
            .options(selectinload(VesselActivity.vessel), selectinload(VesselActivity.comments).selectinload(VesselActivityComment.recorder))
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
            op = await db.get(Operation, activity.operation_id)
            if op and op.status == OperationStatus.vessel_operations:
                remaining_result = await db.execute(
                    select(func.count()).select_from(VesselActivity).where(
                        and_(
                            VesselActivity.operation_id == activity.operation_id,
                            VesselActivity.status != VesselActivityStatus.cancelled,
                            # stage IS NULL (not yet started) must count as
                            # "remaining" too — a plain != excludes NULLs in
                            # SQL's three-valued logic, which would silently
                            # drop not-yet-started activities from the count
                            # and trigger the auto-transition prematurely.
                            or_(
                                VesselActivity.stage.is_(None),
                                VesselActivity.stage != VesselStage.discharge_completed,
                            ),
                        )
                    )
                )
                if (remaining_result.scalar() or 0) == 0:
                    await _transition_operation(
                        op, OperationStatus.pending_completion, current_user, db,
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
    async def record_hse(activity_id: UUID, data: RecordHseRequest, current_user: User, db: AsyncSession) -> VesselActivity:
        """Non-blocking safety record — a failed item never blocks progress
        or any subsequent stage/BDN/completion action."""
        activity = await VesselActivityService._get_or_404(activity_id, db)
        VesselActivityService._assert_authorized(activity, current_user)

        activity.hse_checklist = [item.model_dump() for item in data.checklist]
        activity.hse_result = data.result
        activity.hse_conducted_by = current_user.id
        activity.hse_conducted_at = datetime.utcnow()
        activity.hse_notes = data.notes

        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=activity.operation_id, action="RECORD_VESSEL_HSE",
            entity_type="vessel_activity", entity_id=activity.id,
            changes={"result": data.result.value, "items": len(data.checklist)},
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
