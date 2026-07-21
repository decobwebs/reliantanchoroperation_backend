from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.bdn import BDN, RobEntry
from app.models.vessel import Vessel
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, BdnStatus, OperationStatus
from app.schemas.bdn import BdnCreate
from app.services.notification_service import notify
from app.services.state_machine import StateMachine, StateMachineError
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
            operation.type, operation.status, to_status, current_user.acting_as_role or current_user.role
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

        bdn = BDN(
            bdn_number=bdn_number,
            operation_id=operation_id,
            vessel_id=data.vessel_id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            quantity_delivered_mt=data.quantity_delivered_mt,
            product_type=data.product_type,
            density=data.density,
            temperature=data.temperature,
            delivery_date=data.delivery_date,
            notes=data.notes,
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
