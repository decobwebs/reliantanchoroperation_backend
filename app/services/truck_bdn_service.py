import logging
from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from fastapi import HTTPException, status

from app.models.truck import TruckBdn, TruckOperation
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, BdnStatus, OperationStatus
from app.schemas.truck_bdn import TruckBdnCreate, TruckBdnUpdate
from app.services.notification_service import notify
from app.services.audit_diff import capture_diff
from app.services.state_machine import StateMachine, StateMachineError
from app.services.email_service import email_truck_bdn_submitted
from app.utils.number_generator import generate_truck_bdn_number

logger = logging.getLogger("raoms.truck_bdn")


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

    db.add(OperationStatusHistory(
        operation_id=operation.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=current_user.id,
        reason=reason,
        metadata_={},
    ))


class TruckBdnService:

    @staticmethod
    async def list_truck_bdns(
        operation_id: UUID,
        db: AsyncSession,
    ) -> List[TruckBdn]:
        await _get_operation_or_404(operation_id, db)

        stmt = (
            select(TruckBdn)
            .where(TruckBdn.operation_id == operation_id)
            .order_by(TruckBdn.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_truck_bdn(
        operation_id: UUID,
        data: TruckBdnCreate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckBdn:
        operation = await _get_operation_or_404(operation_id, db)

        # One active Truck BDN at a time per operation — a truck operation is a
        # single delivery event, unlike vessel operations which can have several
        # partial-delivery BDNs.
        existing_result = await db.execute(
            select(TruckBdn.id).where(
                and_(
                    TruckBdn.operation_id == operation_id,
                    TruckBdn.status.in_([BdnStatus.pending, BdnStatus.approved]),
                )
            )
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A Truck BDN is already pending or approved for this operation",
            )

        # Independently compute what the system has on record — never used to
        # fill or default anything the submitter enters, only stored alongside
        # it so the Bunker Manager can compare submitted vs. system-recorded.
        agg_result = await db.execute(
            select(
                func.coalesce(func.sum(TruckOperation.quantity_loaded_mt), 0),
                func.coalesce(func.sum(TruckOperation.quantity_discharged_mt), 0),
                func.min(TruckOperation.discharge_start_at),
                func.max(TruckOperation.discharge_end_at),
            ).where(TruckOperation.operation_id == operation_id)
        )
        system_loaded, system_discharged, system_commenced_at, system_completed_at = agg_result.one()

        system_product_type_result = await db.execute(
            select(TruckOperation.product_type)
            .where(and_(TruckOperation.operation_id == operation_id, TruckOperation.product_type.is_not(None)))
            .limit(1)
        )
        system_product_type = system_product_type_result.scalar_one_or_none()

        system_location_result = await db.execute(
            select(TruckOperation.discharge_location)
            .where(and_(TruckOperation.operation_id == operation_id, TruckOperation.discharge_location.is_not(None)))
            .limit(1)
        )
        system_discharge_location = system_location_result.scalar_one_or_none()

        truck_bdn_number = await generate_truck_bdn_number(db)

        truck_bdn = TruckBdn(
            truck_bdn_number=truck_bdn_number,
            operation_id=operation_id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            company_name=data.company_name,
            product_type=data.product_type,
            discharge_location=data.discharge_location,
            quantity_loaded_mt=data.quantity_loaded_mt,
            quantity_discharged_mt=data.quantity_discharged_mt,
            variance_mt=data.quantity_loaded_mt - data.quantity_discharged_mt,
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
            system_discharge_location=system_discharge_location,
            system_quantity_loaded_mt=system_loaded,
            system_quantity_discharged_mt=system_discharged,
            system_discharge_commenced_at=system_commenced_at,
            system_discharge_completed_at=system_completed_at,
            notes=data.notes,
        )
        db.add(truck_bdn)
        await db.flush()

        # Transition operation to bdn_pending (no-op if already there)
        if operation.status != OperationStatus.bdn_pending:
            await _transition_operation(
                operation, OperationStatus.bdn_pending, current_user, db,
                reason="Truck BDN submitted"
            )

        # Notify + email Bunker Manager (needs to approve) and Finance Manager
        # (heads-up — will need to invoice once approved).
        recipients_result = await db.execute(
            select(User).where(User.role.in_([UserRole.bunker_manager, UserRole.finance_manager]))
        )
        recipients = recipients_result.scalars().all()
        for recipient in recipients:
            await notify(
                db=db,
                user_id=recipient.id,
                type_="bdn_ready",
                title="Truck BDN Ready for Review",
                message=f"Truck BDN {truck_bdn_number} for operation {operation.operation_number} is ready for review",
                priority="high" if recipient.role == UserRole.bunker_manager else "normal",
                operation_id=operation_id,
                action_url=f"/truck-bdns/{truck_bdn.id}",
                channels=["in_app", "whatsapp"],
                wa_template="bdn_submitted",
                wa_kwargs={
                    "operation_number": operation.operation_number,
                    "bdn_number": truck_bdn_number,
                    "quantity": str(data.quantity_discharged_mt),
                },
            )
            try:
                await email_truck_bdn_submitted(
                    to_email=recipient.email,
                    recipient_name=recipient.full_name,
                    operation_number=operation.operation_number,
                    truck_bdn_number=truck_bdn_number,
                    quantity_loaded=str(data.quantity_loaded_mt),
                    quantity_discharged=str(data.quantity_discharged_mt),
                )
            except Exception as exc:
                logger.warning("create_truck_bdn: email failed for %s: %s", recipient.email, exc)

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_TRUCK_BDN",
            entity_type="truck_bdn",
            entity_id=truck_bdn.id,
            changes={
                "truck_bdn_number": truck_bdn_number,
                "quantity_loaded_mt": str(data.quantity_loaded_mt),
                "quantity_discharged_mt": str(data.quantity_discharged_mt),
                "company_name": data.company_name,
                "system_quantity_loaded_mt": str(system_loaded),
                "system_quantity_discharged_mt": str(system_discharged),
            },
        ))

        await db.flush()
        await db.refresh(truck_bdn)

        truck_bdn._generated_by_name = current_user.full_name
        return truck_bdn

    @staticmethod
    async def get_truck_bdn(
        truck_bdn_id: UUID,
        db: AsyncSession,
    ) -> TruckBdn:
        result = await db.execute(select(TruckBdn).where(TruckBdn.id == truck_bdn_id))
        truck_bdn = result.scalar_one_or_none()
        if not truck_bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck BDN not found")
        return truck_bdn

    @staticmethod
    async def update_truck_bdn(
        truck_bdn_id: UUID,
        data: TruckBdnUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> TruckBdn:
        """Bunker Manager corrects any field on a Truck BDN. Mistakes are
        corrected, not recreated — allowed regardless of status."""
        truck_bdn = await TruckBdnService.get_truck_bdn(truck_bdn_id, db)

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        if update_data.get("discharge_completed_at") and "discharge_completion_date" not in update_data:
            update_data["discharge_completion_date"] = update_data["discharge_completed_at"].date()

        changes = capture_diff(truck_bdn, update_data)

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=truck_bdn.operation_id,
            action="UPDATE_TRUCK_BDN",
            entity_type="truck_bdn",
            entity_id=truck_bdn.id,
            changes=changes,
            reason=data.reason,
        ))

        await db.flush()
        await db.refresh(truck_bdn)
        return truck_bdn

    @staticmethod
    async def approve_truck_bdn(
        truck_bdn_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> TruckBdn:
        truck_bdn = await TruckBdnService.get_truck_bdn(truck_bdn_id, db)

        if truck_bdn.status != BdnStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot approve Truck BDN with status '{truck_bdn.status.value}'",
            )

        truck_bdn.status = BdnStatus.approved
        truck_bdn.reviewed_by = current_user.id
        truck_bdn.approved_at = datetime.utcnow()

        operation = await _get_operation_or_404(truck_bdn.operation_id, db)
        if operation.status != OperationStatus.bdn_approved:
            await _transition_operation(
                operation, OperationStatus.bdn_approved, current_user, db,
                reason="Truck BDN approved by bunker manager"
            )

        # Notify Finance Manager
        fm_result = await db.execute(select(User).where(User.role == UserRole.finance_manager))
        for fm in fm_result.scalars().all():
            await notify(
                db=db,
                user_id=fm.id,
                type_="approved",
                title="Truck BDN Approved — Invoice Can Be Generated",
                message=f"Truck BDN {truck_bdn.truck_bdn_number} for operation {operation.operation_number} has been approved. Invoice can now be generated.",
                priority="normal",
                operation_id=truck_bdn.operation_id,
                action_url=f"/truck-bdns/{truck_bdn_id}",
            )

        # Notify the submitter
        await notify(
            db=db,
            user_id=truck_bdn.generated_by,
            type_="approved",
            title="Your Truck BDN Has Been Approved",
            message=f"Truck BDN {truck_bdn.truck_bdn_number} has been approved by the bunker manager",
            priority="normal",
            operation_id=truck_bdn.operation_id,
            action_url=f"/truck-bdns/{truck_bdn_id}",
            channels=["in_app", "whatsapp"],
            wa_template="bdn_approved",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "bdn_number": truck_bdn.truck_bdn_number,
            },
        )

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=truck_bdn.operation_id,
            action="APPROVE_TRUCK_BDN",
            entity_type="truck_bdn",
            entity_id=truck_bdn.id,
            changes={"status": {"from": "pending", "to": "approved"}},
        ))

        await db.flush()
        await db.refresh(truck_bdn)
        return truck_bdn

    @staticmethod
    async def reject_truck_bdn(
        truck_bdn_id: UUID,
        reason: str,
        current_user: User,
        db: AsyncSession,
    ) -> TruckBdn:
        if not reason or len(reason.strip()) < 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Rejection reason must be at least 10 characters",
            )

        truck_bdn = await TruckBdnService.get_truck_bdn(truck_bdn_id, db)

        if truck_bdn.status != BdnStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot reject Truck BDN with status '{truck_bdn.status.value}'",
            )

        truck_bdn.status = BdnStatus.rejected
        truck_bdn.reviewed_by = current_user.id
        truck_bdn.rejection_reason = reason.strip()

        # Back to pending_completion so the submitter can revise and resubmit.
        operation = await _get_operation_or_404(truck_bdn.operation_id, db)
        await _transition_operation(
            operation, OperationStatus.pending_completion, current_user, db,
            reason=f"Truck BDN rejected: {reason}"
        )

        await notify(
            db=db,
            user_id=truck_bdn.generated_by,
            type_="rejected",
            title="Truck BDN Rejected",
            message=f"Truck BDN {truck_bdn.truck_bdn_number} has been rejected. Reason: {reason}",
            priority="high",
            operation_id=truck_bdn.operation_id,
            action_url=f"/truck-bdns/{truck_bdn_id}",
            channels=["in_app", "whatsapp"],
            wa_template="bdn_rejected",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "bdn_number": truck_bdn.truck_bdn_number,
                "reason": reason,
            },
        )

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=truck_bdn.operation_id,
            action="REJECT_TRUCK_BDN",
            entity_type="truck_bdn",
            entity_id=truck_bdn.id,
            changes={"status": {"from": "pending", "to": "rejected"}, "reason": reason},
        ))

        await db.flush()
        await db.refresh(truck_bdn)
        return truck_bdn

    @staticmethod
    async def get_all_truck_bdns(
        page: int,
        per_page: int,
        db: AsyncSession,
    ) -> Tuple[List[TruckBdn], int]:
        count_result = await db.execute(select(func.count()).select_from(TruckBdn))
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        stmt = (
            select(TruckBdn)
            .order_by(TruckBdn.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total
