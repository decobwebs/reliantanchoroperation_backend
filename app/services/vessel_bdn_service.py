import logging
from typing import List
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.bdn import BDN, VesselActivity
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, BdnStatus, OperationStatus, VesselActivityStatus, VesselStage
from app.schemas.vessel_bdn import VesselBdnCreate, VesselBdnUpdate
from app.services.notification_service import notify
from app.services.audit_diff import capture_diff
from app.services.state_machine import StateMachine, StateMachineError
from app.services.email_service import email_vessel_bdn_submitted
from app.utils.number_generator import generate_bdn_number

logger = logging.getLogger("raoms.vessel_bdn")


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _get_vessel_activity_or_404(vessel_activity_id: UUID, db: AsyncSession) -> VesselActivity:
    activity = await db.get(VesselActivity, vessel_activity_id)
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel activity not found")
    return activity


async def _transition_operation(
    operation: Operation, to_status: OperationStatus, current_user: User, db: AsyncSession, reason: str = "",
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
        operation_id=operation.id, from_status=from_status, to_status=to_status,
        changed_by=current_user.id, reason=reason, metadata_={},
    ))


class VesselBdnService:

    @staticmethod
    async def list_vessel_bdns(operation_id: UUID, db: AsyncSession) -> List[BDN]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(BDN)
            .where(and_(BDN.operation_id == operation_id, BDN.vessel_activity_id.is_not(None)))
            .options(selectinload(BDN.generator))
            .order_by(BDN.created_at.desc())
        )
        bdns = list(result.scalars().all())
        for bdn in bdns:
            bdn._generated_by_name = bdn.generator.full_name if bdn.generator else None
        return bdns

    @staticmethod
    async def create_vessel_bdn(vessel_activity_id: UUID, data: VesselBdnCreate, current_user: User, db: AsyncSession) -> BDN:
        activity = await _get_vessel_activity_or_404(vessel_activity_id, db)
        operation = await _get_operation_or_404(activity.operation_id, db)

        # The one hard gate this whole flow protects: a BDN can't even be
        # submitted, let alone approved, until this specific vessel run has
        # actually finished discharging.
        if activity.stage != VesselStage.discharge_completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot submit a Vessel BDN — this vessel run has not reached discharge_completed yet "
                       f"(current stage: {activity.stage.value if activity.stage else 'not started'})",
            )

        # One active BDN per vessel run — mirrors Truck BDN's per-operation
        # uniqueness check, just scoped one level narrower.
        existing_result = await db.execute(
            select(BDN.id).where(
                and_(
                    BDN.vessel_activity_id == vessel_activity_id,
                    BDN.status.in_([BdnStatus.pending, BdnStatus.approved]),
                )
            )
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A Vessel BDN is already pending or approved for this vessel run",
            )

        # Independently compute what the system has on record for THIS vessel
        # run — never used to fill or default anything the submitter enters.
        system_product_type = activity.product_type
        system_quantity_loaded = activity.vessel_received_mt
        system_quantity_discharged = activity.quantity_discharged_mt
        system_commenced_at = activity.stage_discharging_at
        system_completed_at = activity.stage_discharge_completed_at

        bdn_number = await generate_bdn_number(db)

        bdn = BDN(
            bdn_number=bdn_number,
            operation_id=operation.id,
            vessel_id=activity.vessel_id,
            vessel_activity_id=vessel_activity_id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            # Legacy required columns — kept populated for backward compat.
            quantity_delivered_mt=data.quantity_discharged_litres,
            delivery_date=data.discharge_completed_at,
            company_name=data.company_name,
            product_type=data.product_type,
            discharge_location=data.discharge_location,
            receiving_vessel=data.receiving_vessel,
            quantity_loaded_litres=data.quantity_loaded_litres,
            quantity_discharged_litres=data.quantity_discharged_litres,
            variance_litres=data.quantity_loaded_litres - data.quantity_discharged_litres,
            density=data.density,
            temperature_before_loading=data.temperature_before_loading,
            temperature_after_loading=data.temperature_after_loading,
            vcf=data.vcf,
            gov=data.gov,
            gsv=data.gsv,
            mt_vacuum=data.mt_vacuum,
            discharge_commenced_at=data.discharge_commenced_at,
            discharge_completed_at=data.discharge_completed_at,
            discharge_completion_date=data.discharge_completion_date,
            system_product_type=system_product_type,
            system_quantity_loaded_litres=system_quantity_loaded,
            system_quantity_discharged_litres=system_quantity_discharged,
            system_discharge_commenced_at=system_commenced_at,
            system_discharge_completed_at=system_completed_at,
            notes=data.notes,
        )
        db.add(bdn)
        await db.flush()

        # Transition operation to bdn_pending (no-op if already there).
        if operation.status != OperationStatus.bdn_pending:
            await _transition_operation(operation, OperationStatus.bdn_pending, current_user, db, reason="Vessel BDN submitted")

        # Notify + email Bunker Manager (needs to approve) and Finance Manager (heads-up).
        recipients_result = await db.execute(
            select(User).where(User.role.in_([UserRole.bunker_manager, UserRole.finance_manager]))
        )
        for recipient in recipients_result.scalars().all():
            await notify(
                db=db, user_id=recipient.id, type_="bdn_ready",
                title="Vessel BDN Ready for Review",
                message=f"Vessel BDN {bdn_number} for operation {operation.operation_number} (activity {activity.activity_number}) is ready for review",
                priority="high" if recipient.role == UserRole.bunker_manager else "normal",
                operation_id=operation.id, action_url=f"/operations/{operation.id}",
                channels=["in_app", "whatsapp"], wa_template="bdn_submitted",
                wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn_number, "quantity": str(data.quantity_discharged_litres)},
            )
            try:
                await email_vessel_bdn_submitted(
                    to_email=recipient.email, recipient_name=recipient.full_name,
                    operation_number=operation.operation_number, vessel_bdn_number=bdn_number,
                    quantity_loaded=str(data.quantity_loaded_litres), quantity_discharged=str(data.quantity_discharged_litres),
                )
            except Exception as exc:
                logger.warning("create_vessel_bdn: email failed for %s: %s", recipient.email, exc)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="CREATE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={
                "bdn_number": bdn_number, "vessel_activity_id": str(vessel_activity_id),
                "quantity_loaded_litres": str(data.quantity_loaded_litres),
                "quantity_discharged_litres": str(data.quantity_discharged_litres),
                "system_quantity_loaded_litres": str(system_quantity_loaded) if system_quantity_loaded is not None else None,
                "system_quantity_discharged_litres": str(system_quantity_discharged) if system_quantity_discharged is not None else None,
            },
        ))

        await db.flush()
        await db.refresh(bdn)
        bdn._generated_by_name = current_user.full_name
        return bdn

    @staticmethod
    async def get_vessel_bdn(bdn_id: UUID, db: AsyncSession) -> BDN:
        result = await db.execute(
            select(BDN).where(BDN.id == bdn_id).options(selectinload(BDN.generator))
        )
        bdn = result.scalar_one_or_none()
        if not bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel BDN not found")
        bdn._generated_by_name = bdn.generator.full_name if bdn.generator else None
        return bdn

    @staticmethod
    async def update_vessel_bdn(bdn_id: UUID, data: VesselBdnUpdate, current_user: User, db: AsyncSession) -> BDN:
        """Bunker Manager corrects any field — allowed regardless of status."""
        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        if update_data.get("discharge_completed_at") and "discharge_completion_date" not in update_data:
            update_data["discharge_completion_date"] = update_data["discharge_completed_at"].date()

        # Keep variance_litres consistent with whichever quantity figures
        # the BM just corrected — it's derived, never independently edited.
        if "quantity_loaded_litres" in update_data or "quantity_discharged_litres" in update_data:
            new_loaded = update_data.get("quantity_loaded_litres", bdn.quantity_loaded_litres)
            new_discharged = update_data.get("quantity_discharged_litres", bdn.quantity_discharged_litres)
            update_data["variance_litres"] = new_loaded - new_discharged

        changes = capture_diff(bdn, update_data)
        db.add(AuditLog(
            user_id=current_user.id, operation_id=bdn.operation_id, action="UPDATE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id, changes=changes, reason=data.reason,
        ))
        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def _approval_progress(operation_id: UUID, db: AsyncSession) -> tuple[int, int]:
        """(total vessel runs, vessel runs with an approved BDN) — cancelled
        runs don't count toward the total."""
        total_result = await db.execute(
            select(func.count()).select_from(VesselActivity).where(
                and_(VesselActivity.operation_id == operation_id, VesselActivity.status != VesselActivityStatus.cancelled)
            )
        )
        total = total_result.scalar() or 0

        approved_result = await db.execute(
            select(func.count(func.distinct(BDN.vessel_activity_id))).select_from(BDN).join(
                VesselActivity, BDN.vessel_activity_id == VesselActivity.id
            ).where(
                and_(
                    VesselActivity.operation_id == operation_id,
                    VesselActivity.status != VesselActivityStatus.cancelled,
                    BDN.status == BdnStatus.approved,
                )
            )
        )
        approved = approved_result.scalar() or 0
        return total, approved

    @staticmethod
    async def approve_vessel_bdn(bdn_id: UUID, current_user: User, db: AsyncSession) -> tuple[BDN, int, int, bool]:
        """Approves this one BDN, then checks whether EVERY vessel run on the
        operation now has an approved BDN — only then does the operation
        transition to bdn_approved. Never trust the UI alone for this gate.
        Returns (bdn, total_runs, approved_runs, operation_completed_gate_cleared)."""
        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)

        if bdn.status != BdnStatus.pending:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot approve Vessel BDN with status '{bdn.status.value}'")

        bdn.status = BdnStatus.approved
        bdn.reviewed_by = current_user.id
        bdn.approved_at = datetime.utcnow()
        await db.flush()

        operation = await _get_operation_or_404(bdn.operation_id, db)
        total, approved = await VesselBdnService._approval_progress(operation.id, db)
        gate_cleared = total > 0 and approved >= total

        if gate_cleared and operation.status != OperationStatus.bdn_approved:
            await _transition_operation(operation, OperationStatus.bdn_approved, current_user, db, reason="All vessel run BDNs approved")

        fm_result = await db.execute(select(User).where(User.role == UserRole.finance_manager))
        for fm in fm_result.scalars().all():
            await notify(
                db=db, user_id=fm.id, type_="approved",
                title="Vessel BDN Approved" + (" — Invoice Can Be Generated" if gate_cleared else ""),
                message=f"Vessel BDN {bdn.bdn_number} for operation {operation.operation_number} has been approved."
                        + (" All vessel runs are now approved — invoice can be generated." if gate_cleared else f" {approved} of {total} vessel run(s) approved so far."),
                priority="normal", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            )

        await notify(
            db=db, user_id=bdn.generated_by, type_="approved",
            title="Your Vessel BDN Has Been Approved",
            message=f"Vessel BDN {bdn.bdn_number} has been approved by the bunker manager",
            priority="normal", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"], wa_template="bdn_approved",
            wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn.bdn_number},
        )

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="APPROVE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "approved"}, "approved_runs": f"{approved}/{total}"},
        ))

        await db.flush()
        await db.refresh(bdn)
        return bdn, total, approved, gate_cleared

    @staticmethod
    async def reject_vessel_bdn(bdn_id: UUID, reason: str, current_user: User, db: AsyncSession) -> BDN:
        if not reason or len(reason.strip()) < 10:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rejection reason must be at least 10 characters")

        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)
        if bdn.status != BdnStatus.pending:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot reject Vessel BDN with status '{bdn.status.value}'")

        bdn.status = BdnStatus.rejected
        bdn.reviewed_by = current_user.id
        bdn.rejection_reason = reason.strip()

        operation = await _get_operation_or_404(bdn.operation_id, db)
        await _transition_operation(operation, OperationStatus.vessel_operations, current_user, db, reason=f"Vessel BDN rejected: {reason}")

        await notify(
            db=db, user_id=bdn.generated_by, type_="rejected",
            title="Vessel BDN Rejected",
            message=f"Vessel BDN {bdn.bdn_number} has been rejected. Reason: {reason}",
            priority="high", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"], wa_template="bdn_rejected",
            wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn.bdn_number, "reason": reason},
        )

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="REJECT_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "rejected"}, "reason": reason},
        ))

        await db.flush()
        await db.refresh(bdn)
        return bdn
