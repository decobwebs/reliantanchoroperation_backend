from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.bdn import BDN, RobEntry
from app.models.vessel import Vessel
from app.models.truck import TruckOperation
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, BdnStatus, OperationStatus, OperationType, TruckOpStatus, RobEntryType
from app.schemas.bdn import BdnCreate, BdnUpdate
from app.services.notification_service import notify
from app.services.state_machine import StateMachine, StateMachineError, acting_role
from app.utils.number_generator import generate_bdn_number


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(
            and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
        )
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _transition_operation(
    operation: Operation,
    to_status: OperationStatus,
    current_user: User,
    db: AsyncSession,
    reason: str = "",
) -> None:
    try:
        StateMachine.validate_transition(
            operation.type, operation.status, to_status, acting_role(current_user)
        )
    except StateMachineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    from_status = operation.status
    operation.status = to_status
    operation.updated_at = datetime.utcnow()

    history = OperationStatusHistory(
        operation_id=operation.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=current_user.id,
        reason=reason,
        metadata_={},
    )
    db.add(history)


class BdnService:

    @staticmethod
    async def list_bdns(
        operation_id: UUID,
        db: AsyncSession,
    ) -> List[BDN]:
        await _get_operation_or_404(operation_id, db)

        stmt = (
            select(BDN)
            .where(BDN.operation_id == operation_id)
            .order_by(BDN.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_bdn(
        operation_id: UUID,
        data: BdnCreate,
        current_user: User,
        db: AsyncSession,
    ) -> BDN:
        operation = await _get_operation_or_404(operation_id, db)

        # Verify vessel exists
        vessel_result = await db.execute(
            select(Vessel).where(Vessel.id == data.vessel_id)
        )
        vessel = vessel_result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")

        bdn_number = await generate_bdn_number(db)

        # Trucks-discharged is computed here, never client-supplied — display
        # only (see BdnOut), same convention as the equivalent field on the
        # Vessel BDN flow. Full Operation only: a truck_only op has no vessel
        # deliveries to reconcile against, and vessel_only never routes
        # product through a truck in the first place.
        truck_discharged_total_mt = None
        truck_variance_mt = None
        if operation.type == OperationType.full_operation:
            truck_total_result = await db.execute(
                select(func.coalesce(func.sum(TruckOperation.quantity_discharged_mt), 0)).where(
                    and_(
                        TruckOperation.operation_id == operation.id,
                        TruckOperation.status == TruckOpStatus.completed,
                        TruckOperation.destination_vessel_id == data.vessel_id,
                    )
                )
            )
            truck_discharged_total_mt = truck_total_result.scalar() or 0
            truck_variance_mt = data.discharge_gov - truck_discharged_total_mt

        bdn = BDN(
            bdn_number=bdn_number,
            operation_id=operation_id,
            vessel_id=data.vessel_id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            quantity_delivered_mt=data.quantity_delivered_mt,
            discharge_gov=data.discharge_gov,
            discharge_gsv=data.discharge_gsv,
            product_type=data.product_type,
            density=data.density,
            temperature=data.temperature,
            delivery_date=data.delivery_date,
            notes=data.notes,
            truck_discharged_total_mt=truck_discharged_total_mt,
            truck_variance_mt=truck_variance_mt,
            version=1,
        )
        db.add(bdn)
        await db.flush()

        # Transition operation to bdn_pending (no-op if already there — BM may have set it manually)
        if operation.status != OperationStatus.bdn_pending:
            await _transition_operation(
                operation, OperationStatus.bdn_pending, current_user, db,
                reason="BDN created by marine manager"
            )

        # Notify BM
        bm_result = await db.execute(
            select(User).where(User.role == UserRole.bunker_manager)
        )
        bm_users = bm_result.scalars().all()
        for bm in bm_users:
            await notify(
                db=db,
                user_id=bm.id,
                type_="bdn_ready",
                title="BDN Ready for Review",
                message=f"BDN {bdn_number} for operation {operation.operation_number} is ready for your review",
                priority="high",
                operation_id=operation_id,
                action_url=f"/bdns/{bdn.id}",
                channels=["in_app", "whatsapp"],
                wa_template="bdn_submitted",
                wa_kwargs={
                    "operation_number": operation.operation_number,
                    "bdn_number": bdn_number,
                    "quantity": str(data.quantity_delivered_mt),
                },
            )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_BDN",
            entity_type="bdn",
            entity_id=bdn.id,
            changes={
                "bdn_number": bdn_number,
                "quantity_delivered_mt": str(data.quantity_delivered_mt),
                "vessel_id": str(data.vessel_id),
            },
        )
        db.add(audit)

        await db.flush()
        await db.refresh(bdn)

        # Attach computed fields
        bdn._vessel_name = vessel.vessel_name
        bdn._generated_by_name = current_user.full_name

        return bdn

    @staticmethod
    async def get_bdn(
        bdn_id: UUID,
        db: AsyncSession,
    ) -> BDN:
        result = await db.execute(select(BDN).where(BDN.id == bdn_id))
        bdn = result.scalar_one_or_none()
        if not bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BDN not found")
        return bdn

    @staticmethod
    async def approve_bdn(
        bdn_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> BDN:
        result = await db.execute(select(BDN).where(BDN.id == bdn_id))
        bdn = result.scalar_one_or_none()
        if not bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BDN not found")

        if bdn.status != BdnStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot approve BDN with status '{bdn.status.value}'",
            )

        bdn.status = BdnStatus.approved
        bdn.reviewed_by = current_user.id
        bdn.approved_at = datetime.utcnow()

        # Transition operation to bdn_approved (no-op if already there — multiple BDN scenario)
        operation = await _get_operation_or_404(bdn.operation_id, db)

        # Credit the vessel's ROB — quantity_delivered_mt is this BDN's one
        # manual figure of record. Only fires from here forward: this method
        # requires status == pending on entry, so an already-approved BDN can
        # never pass through it a second time, and nothing here touches BDNs
        # approved before this credit existed — no backfill, no risk of
        # double-counting whatever ROB value they already contributed to.
        if operation.type == OperationType.full_operation:
            await BdnService._apply_rob_credit(bdn, current_user, db)

        if operation.status != OperationStatus.bdn_approved:
            await _transition_operation(
                operation, OperationStatus.bdn_approved, current_user, db,
                reason="BDN approved by bunker manager"
            )

        # Notify Finance Manager
        fm_result = await db.execute(
            select(User).where(User.role == UserRole.finance_manager)
        )
        fm_users = fm_result.scalars().all()
        for fm in fm_users:
            await notify(
                db=db,
                user_id=fm.id,
                type_="approved",
                title="BDN Approved — Invoice Can Be Generated",
                message=f"BDN {bdn.bdn_number} for operation {operation.operation_number} has been approved. Invoice can now be generated.",
                priority="normal",
                operation_id=bdn.operation_id,
                action_url=f"/bdns/{bdn_id}",
            )

        # Notify Marine Manager (generator)
        await notify(
            db=db,
            user_id=bdn.generated_by,
            type_="approved",
            title="Your BDN Has Been Approved",
            message=f"BDN {bdn.bdn_number} has been approved by the bunker manager",
            priority="normal",
            operation_id=bdn.operation_id,
            action_url=f"/bdns/{bdn_id}",
            channels=["in_app", "whatsapp"],
            wa_template="bdn_approved",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "bdn_number": bdn.bdn_number,
            },
        )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=bdn.operation_id,
            action="APPROVE_BDN",
            entity_type="bdn",
            entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "approved"}},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def update_bdn(
        bdn_id: UUID,
        data: BdnUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> BDN:
        """Bunker Manager corrects any field — allowed regardless of status.
        quantity_delivered_mt is the one that touches ROB: if this BDN is
        already approved, the old credit is reversed and the corrected
        figure reapplied, same reverse-then-reapply pattern used for the
        Vessel BDN flow (see VesselBdnService.update_vessel_bdn)."""
        bdn = await BdnService.get_bdn(bdn_id, db)

        operation = await _get_operation_or_404(bdn.operation_id, db)
        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        old_qty = bdn.quantity_delivered_mt

        rob_needs_reapply = (
            operation.type == OperationType.full_operation
            and bdn.status == BdnStatus.approved
            and "quantity_delivered_mt" in update_data
            and update_data["quantity_delivered_mt"] != old_qty
        )
        if rob_needs_reapply:
            await BdnService._reverse_rob_credit(bdn, db)

        changes: dict = {"edited_by": current_user.full_name}
        for field, value in update_data.items():
            changes[field] = {"from": str(getattr(bdn, field)), "to": str(value)}
            setattr(bdn, field, value)

        # Truck variance recomputes with a corrected GOV, same formula as creation.
        if "discharge_gov" in update_data and bdn.truck_discharged_total_mt is not None:
            bdn.truck_variance_mt = bdn.discharge_gov - bdn.truck_discharged_total_mt

        if rob_needs_reapply:
            await BdnService._apply_rob_credit(bdn, current_user, db)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=bdn.operation_id, action="UPDATE_BDN",
            entity_type="bdn", entity_id=bdn.id, changes=changes, reason=data.reason,
        ))
        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def _apply_rob_credit(bdn: BDN, current_user: User, db: AsyncSession) -> None:
        vessel = await db.get(Vessel, bdn.vessel_id)
        if not vessel:
            return
        rob_before = vessel.current_rob_mt or 0
        rob_after = rob_before + bdn.quantity_delivered_mt
        vessel.current_rob_mt = rob_after
        db.add(RobEntry(
            vessel_id=bdn.vessel_id, operation_id=bdn.operation_id,
            entry_type=RobEntryType.replenishment, quantity_mt=bdn.quantity_delivered_mt,
            rob_before_mt=rob_before, rob_after_mt=rob_after, recorded_by=current_user.id,
            source_description=f"BDN {bdn.bdn_number}",
            notes=f"BDN {bdn.bdn_number} approved",
        ))
        await db.flush()

    @staticmethod
    async def _reverse_rob_credit(bdn: BDN, db: AsyncSession) -> None:
        """Delete-and-reinsert reversal — finds this BDN's prior RobEntry
        rows by their exact source_description marker, subtracts their net
        effect off the vessel's current ROB, deletes them. Caller writes the
        fresh entry afterward if one is still due."""
        vessel = await db.get(Vessel, bdn.vessel_id)
        if not vessel:
            return
        prior_result = await db.execute(
            select(RobEntry).where(
                and_(RobEntry.vessel_id == bdn.vessel_id, RobEntry.source_description == f"BDN {bdn.bdn_number}")
            )
        )
        prior_entries = list(prior_result.scalars().all())
        if not prior_entries:
            return
        reversed_net = sum((e.quantity_mt for e in prior_entries), type(prior_entries[0].quantity_mt)(0))
        vessel.current_rob_mt = (vessel.current_rob_mt or 0) - reversed_net
        for entry in prior_entries:
            await db.delete(entry)
        await db.flush()

    @staticmethod
    async def delete_bdn(
        bdn_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> None:
        """Bunker Manager deletes a BDN outright — for a wrong or test entry
        that shouldn't just be rejected (rejected still keeps the record).
        If it was approved, its ROB credit is reversed first so deleting it
        never leaves a phantom amount on the vessel's ledger."""
        bdn = await BdnService.get_bdn(bdn_id, db)
        if bdn.status == BdnStatus.approved:
            await BdnService._reverse_rob_credit(bdn, db)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=bdn.operation_id, action="DELETE_BDN",
            entity_type="bdn", entity_id=bdn.id,
            changes={"bdn_number": bdn.bdn_number, "status_at_deletion": bdn.status.value,
                     "quantity_delivered_mt": str(bdn.quantity_delivered_mt)},
        ))
        await db.execute(delete(AuditLog).where(AuditLog.entity_type == "bdn", AuditLog.entity_id == bdn.id, AuditLog.action != "DELETE_BDN"))
        await db.delete(bdn)
        await db.flush()

    @staticmethod
    async def reject_bdn(
        bdn_id: UUID,
        reason: str,
        current_user: User,
        db: AsyncSession,
    ) -> BDN:
        if not reason or len(reason.strip()) < 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Rejection reason must be at least 10 characters",
            )

        result = await db.execute(select(BDN).where(BDN.id == bdn_id))
        bdn = result.scalar_one_or_none()
        if not bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BDN not found")

        if bdn.status != BdnStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot reject BDN with status '{bdn.status.value}'",
            )

        bdn.status = BdnStatus.rejected
        bdn.reviewed_by = current_user.id
        bdn.rejection_reason = reason.strip()

        # Transition operation back to vessel_operations
        operation = await _get_operation_or_404(bdn.operation_id, db)
        await _transition_operation(
            operation, OperationStatus.vessel_operations, current_user, db,
            reason=f"BDN rejected: {reason}"
        )

        # HIGH priority notification to Marine Manager
        await notify(
            db=db,
            user_id=bdn.generated_by,
            type_="rejected",
            title="BDN Rejected",
            message=f"BDN {bdn.bdn_number} has been rejected. Reason: {reason}",
            priority="high",
            operation_id=bdn.operation_id,
            action_url=f"/bdns/{bdn_id}",
            channels=["in_app", "whatsapp"],
            wa_template="bdn_rejected",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "bdn_number": bdn.bdn_number,
                "reason": reason,
            },
        )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=bdn.operation_id,
            action="REJECT_BDN",
            entity_type="bdn",
            entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "rejected"}, "reason": reason},
        )
        db.add(audit)

        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def get_all_bdns(
        page: int,
        per_page: int,
        db: AsyncSession,
    ) -> Tuple[List[BDN], int]:
        count_stmt = select(func.count()).select_from(BDN)
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        stmt = (
            select(BDN)
            .order_by(BDN.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(stmt)
        bdns = list(result.scalars().all())

        return bdns, total
