from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from fastapi import HTTPException, status

from app.models.finance import PFI, Payment, PfiAllocation
from app.models.operation import Operation, OperationStatusHistory
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, OperationType, OperationStatus, PfiStatus, PfiType, VoucherStatus, VoucherCategory
from app.services.audit_diff import capture_diff


async def _notify_role(
    db: AsyncSession,
    role: UserRole,
    operation_id: UUID,
    type_: str,
    title: str,
    message: str,
    priority: str = "normal",
    wa_template: str = None,
    wa_kwargs: dict = None,
) -> None:
    """Notify all active users with the given role (in-app + optional WhatsApp)."""
    result = await db.execute(select(User).where(User.role == role, User.is_active == True))
    channels = ["in_app", "whatsapp"] if wa_template else ["in_app"]
    for user in result.scalars().all():
        await notify(
            db=db,
            user_id=user.id,
            type_=type_,
            title=title,
            message=message,
            priority=priority,
            operation_id=operation_id,
            channels=channels,
            wa_template=wa_template,
            wa_kwargs=wa_kwargs,
        )
from app.schemas.pfi import (
    PfiCreate, PfiUpdate, PfiGenerateRequest, PaymentCreate, StandalonePfiCreate, PfiConfirmPaymentRequest,
    PfiAllocationCreate, PfiAllocationUpdate,
)
from app.services.notification_service import notify
from app.services.state_machine import StateMachine, StateMachineError
from app.utils.number_generator import generate_pfi_number, generate_voucher_number
from app.utils.pfi_pdf import generate_pfi_pdf
from app.services.document_service import _upload_to_supabase


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(
            and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
        )
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return op


async def _transition_operation(
    operation: Operation,
    to_status: OperationStatus,
    current_user: User,
    db: AsyncSession,
    reason: str = "",
) -> None:
    try:
        StateMachine.validate_transition(
            operation.type, operation.status, to_status, current_user.role
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


class PfiService:

    @staticmethod
    async def create_standalone_pfi(
        data: StandalonePfiCreate,
        current_user: User,
        db: AsyncSession,
    ) -> PFI:
        """
        Create a PFI before any operation exists.
        Can be called by BM or FM. operation_id is null until BM creates the operation.
        """
        pfi_number = await generate_pfi_number(db)

        amount_ngn = None
        if data.currency == "NGN":
            amount_ngn = data.amount
        elif data.exchange_rate and data.exchange_rate > 0:
            amount_ngn = data.amount * data.exchange_rate

        pfi = PFI(
            pfi_number=pfi_number,
            operation_id=None,  # standalone — not yet linked
            linked_by=current_user.id,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            amount_ngn=amount_ngn,
            quantity_litres=data.quantity_litres,
            supplier_name=data.supplier_name,
            description=data.description,
            document_url=data.document_url,
            client_ref=data.client_ref,
            pfi_type=PfiType.client_proforma,
            status=PfiStatus.pending,
        )
        db.add(pfi)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=None,
            action="CREATE_STANDALONE_PFI",
            entity_type="pfi",
            entity_id=pfi.id,
            changes={"pfi_number": pfi_number, "amount": str(data.amount), "currency": data.currency},
        ))

        await db.commit()
        await db.refresh(pfi)
        await PfiService._attach_balance(pfi, db)
        return pfi

    @staticmethod
    async def confirm_pfi_payment(
        pfi_id: UUID,
        data: PfiConfirmPaymentRequest,
        current_user: User,
        db: AsyncSession,
    ) -> PFI:
        """
        FM confirms that payment for this PFI has been received.
        Advances PFI status to 'paid' — it is now ready for an operation to be created.
        """
        result = await db.execute(select(PFI).where(PFI.id == pfi_id))
        pfi = result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFI not found")

        if pfi.status in (PfiStatus.paid, PfiStatus.linked, PfiStatus.completed):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PFI is already in '{pfi.status.value}' status",
            )

        pfi.status = PfiStatus.paid
        pfi.confirmed_by = current_user.id
        pfi.confirmed_at = datetime.utcnow()
        if data.receipt_url:
            pfi.receipt_url = data.receipt_url

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=pfi.operation_id,
            action="CONFIRM_PFI_PAYMENT",
            entity_type="pfi",
            entity_id=pfi.id,
            changes={"pfi_number": pfi.pfi_number, "confirmed_by": str(current_user.id)},
        ))

        # Notify BM that PFI is paid and ready for operation
        result_bms = await db.execute(
            select(User).where(User.role == UserRole.bunker_manager, User.is_active == True)
        )
        for bm in result_bms.scalars().all():
            await notify(
                db=db,
                user_id=bm.id,
                type_="payment_update",
                title="PFI Payment Confirmed",
                message=f"PFI {pfi.pfi_number} payment confirmed by Finance. Ready to create operation.",
                priority="high",
                operation_id=pfi.operation_id,
                channels=["in_app"],
            )

        await db.commit()
        await db.refresh(pfi)
        await PfiService._attach_balance(pfi, db)
        return pfi

    @staticmethod
    async def list_all_pfis(
        db: AsyncSession,
        status_filter: Optional[str] = None,
        unlinked_only: bool = False,
    ) -> List[PFI]:
        """Global PFI list — for FM and BM dashboards."""
        stmt = select(PFI).order_by(PFI.created_at.desc())
        if status_filter:
            stmt = stmt.where(PFI.status == status_filter)
        if unlinked_only:
            stmt = stmt.where(PFI.operation_id.is_(None))
        result = await db.execute(stmt)
        pfis = list(result.scalars().all())
        for pfi in pfis:
            await PfiService._attach_balance(pfi, db)
        return pfis

    @staticmethod
    async def link_pfi_to_operation(
        pfi_id: UUID,
        operation_id: UUID,
        db: AsyncSession,
    ) -> PFI:
        """Called internally when an operation is created with a pfi_id."""
        result = await db.execute(select(PFI).where(PFI.id == pfi_id))
        pfi = result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFI not found")
        if pfi.status not in (PfiStatus.paid, PfiStatus.pending, PfiStatus.confirmed):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"PFI must be paid before linking to an operation. Current status: '{pfi.status.value}'",
            )
        if pfi.operation_id is not None and str(pfi.operation_id) != str(operation_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This PFI is already linked to a different operation",
            )
        pfi.operation_id = operation_id
        pfi.status = PfiStatus.linked
        return pfi

    @staticmethod
    async def create_pfi(
        operation_id: UUID,
        data: PfiCreate,
        current_user: User,
        db: AsyncSession,
    ) -> PFI:
        operation = await _get_operation_or_404(operation_id, db)

        pfi_number = await generate_pfi_number(db)

        # Compute NGN amount if exchange rate provided
        amount_ngn = None
        if data.currency == "NGN":
            amount_ngn = data.amount
        elif data.exchange_rate and data.exchange_rate > 0:
            amount_ngn = data.amount * data.exchange_rate

        pfi = PFI(
            pfi_number=pfi_number,
            operation_id=operation_id,
            linked_by=current_user.id,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            amount_ngn=amount_ngn,
            quantity_litres=data.quantity_litres,
            supplier_name=data.supplier_name,
            description=data.description,
            document_url=data.document_url,
            pfi_type=PfiType.client_proforma,
            status=PfiStatus.pending,
        )
        db.add(pfi)
        await db.flush()

        # Transition operation to pfi_linked
        await _transition_operation(
            operation, OperationStatus.pfi_linked, current_user, db,
            reason=f"PFI {pfi_number} linked"
        )

        # Audit log
        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_PFI",
            entity_type="pfi",
            entity_id=pfi.id,
            changes={"pfi_number": pfi_number, "amount": str(data.amount), "currency": data.currency},
        )
        db.add(audit)

        # Notify all Finance Managers (in-app + WhatsApp)
        await _notify_role(
            db=db,
            role=UserRole.finance_manager,
            operation_id=operation_id,
            type_="payment_update",
            title="PFI Ready for Payment",
            message=f"PFI {pfi_number} has been linked to operation {operation.operation_number}. Payment required.",
            priority="high",
            wa_template="pfi_received",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "pfi_number": pfi_number,
                "amount": str(data.amount),
                "currency": data.currency,
            },
        )

        await db.commit()
        await db.refresh(pfi)
        await PfiService._attach_balance(pfi, db)
        return pfi

    @staticmethod
    async def generate_pfi(
        operation_id: UUID,
        data: PfiGenerateRequest,
        current_user: User,
        db: AsyncSession,
    ) -> PFI:
        """Generate a PFI PDF from operation data, upload to storage, and record it."""
        from sqlalchemy.orm import selectinload
        stmt = (
            select(Operation)
            .options(selectinload(Operation.client))
            .where(Operation.id == operation_id, Operation.deleted_at.is_(None))
        )
        op_result = await db.execute(stmt)
        operation = op_result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        pfi_number = await generate_pfi_number(db)
        issue_date = datetime.utcnow()

        # Compute amount from volume × rate
        vol = operation.expected_volume_mt
        rate = data.rate_per_mt
        if vol is None or vol == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Operation must have an expected volume to generate a PFI",
            )
        subtotal = float(vol) * float(rate)
        tax_amt  = subtotal * float(data.tax_rate) / 100
        total    = subtotal + tax_amt

        # Compute NGN amount
        amount_ngn = None
        if operation.currency == "NGN":
            amount_ngn = total
        elif data.exchange_rate and data.exchange_rate > 0:
            amount_ngn = total * float(data.exchange_rate)

        # Generate the PDF
        client = operation.client
        pdf_bytes = generate_pfi_pdf(
            pfi_number=pfi_number,
            operation_number=operation.operation_number,
            operation_type=operation.type.value if hasattr(operation.type, "value") else str(operation.type),
            operation_version=operation.version,
            product_type=operation.product_type,
            loading_location=operation.loading_location,
            discharge_location=operation.discharge_location,
            expected_volume_mt=vol,
            currency=operation.currency,
            rate_per_mt=rate,
            tax_rate=data.tax_rate,
            exchange_rate=data.exchange_rate,
            validity_days=data.validity_days,
            issue_date=issue_date,
            supplier_name=data.supplier_name,
            description=data.description,
            notes=data.notes,
            client_name=client.full_name if client else "—",
            client_email=client.email if client else "—",
            client_phone=client.phone if client else None,
            prepared_by_name=current_user.full_name,
            prepared_by_role=current_user.role.value.replace("_", " ").title(),
        )

        # Upload to Supabase Storage (non-fatal — PFI is created even if storage fails)
        document_url = None
        try:
            storage_path = f"pfis/{operation_id}/{pfi_number}.pdf"
            document_url = await _upload_to_supabase(pdf_bytes, storage_path, "application/pdf")
        except Exception:
            pass

        pfi = PFI(
            pfi_number=pfi_number,
            operation_id=operation_id,
            linked_by=current_user.id,
            amount=total,
            currency=operation.currency,
            exchange_rate=data.exchange_rate,
            amount_ngn=amount_ngn,
            quantity_litres=data.quantity_litres,
            supplier_name=data.supplier_name or "Reliant Anchor Logistics Limited",
            description=data.description,
            document_url=document_url,
            pfi_type=PfiType.client_proforma,
            status=PfiStatus.pending,
        )
        db.add(pfi)
        await db.flush()

        # PFI is issued from the `active` state for all operation types.
        # For operations that pre-date this design (already past bdn_approved),
        # allow attaching a PFI via the compat bdn_approved → pfi_linked path.
        _PRE_PFI_STATES = {OperationStatus.active}
        _COMPAT_PRE_PFI = {OperationStatus.bdn_approved}
        _POST_PFI_STATES = {
            OperationStatus.pfi_linked, OperationStatus.payment_processing,
            OperationStatus.payment_confirmed, OperationStatus.vessel_operations,
            OperationStatus.bdn_pending, OperationStatus.bdn_approved,
            OperationStatus.invoiced, OperationStatus.completed, OperationStatus.archived,
        }
        if operation.status in _PRE_PFI_STATES or operation.status in _COMPAT_PRE_PFI:
            await _transition_operation(
                operation, OperationStatus.pfi_linked, current_user, db,
                reason=f"PFI {pfi_number} generated by system",
            )
        elif operation.status in _POST_PFI_STATES:
            pass  # already in finance phase — just attach the PFI document
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Cannot generate PFI: operation is in '{operation.status.value}'. "
                    f"It must be 'active' before a PFI can be generated."
                ),
            )

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="GENERATE_PFI",
            entity_type="pfi",
            entity_id=pfi.id,
            changes={"pfi_number": pfi_number, "amount": str(total),
                     "currency": operation.currency, "source": "system_generated"},
        ))

        await _notify_role(
            db=db,
            role=UserRole.finance_manager,
            operation_id=operation_id,
            type_="payment_update",
            title="PFI Ready for Payment",
            message=f"PFI {pfi_number} has been generated for operation {operation.operation_number}. Payment required.",
            priority="high",
            wa_template="pfi_received",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "pfi_number": pfi_number,
                "amount": f"{operation.currency} {total:,.2f}",
                "currency": operation.currency,
            },
        )

        await db.commit()
        await db.refresh(pfi)
        await PfiService._attach_balance(pfi, db)
        return pfi

    @staticmethod
    async def list_pfis(operation_id: UUID, db: AsyncSession) -> List[PFI]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(PFI)
            .where(PFI.operation_id == operation_id)
            .order_by(PFI.created_at.asc())
        )
        pfis = list(result.scalars().all())
        for pfi in pfis:
            await PfiService._attach_balance(pfi, db)
        return pfis

    @staticmethod
    async def get_pfi(pfi_id: UUID, db: AsyncSession) -> PFI:
        result = await db.execute(select(PFI).where(PFI.id == pfi_id))
        pfi = result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFI not found")
        await PfiService._attach_balance(pfi, db)
        return pfi

    @staticmethod
    async def update_pfi(
        pfi_id: UUID,
        data: PfiUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> PFI:
        """BM or FM edits a PFI's own fields. Mistakes are corrected, not
        recreated — no status gate on when this can happen."""
        result = await db.execute(select(PFI).where(PFI.id == pfi_id))
        pfi = result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFI not found")

        # Can't shrink quantity_litres below what's already allocated to operations.
        if data.quantity_litres is not None:
            allocated_result = await db.execute(
                select(func.coalesce(func.sum(PfiAllocation.quantity_litres), 0))
                .where(PfiAllocation.pfi_id == pfi_id)
            )
            allocated = Decimal(allocated_result.scalar_one())
            if data.quantity_litres < allocated:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot reduce quantity below {allocated} litres already allocated to operations",
                )

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        changes = capture_diff(pfi, update_data)

        # Recompute amount_ngn if anything affecting it changed.
        if {"amount", "currency", "exchange_rate"} & set(update_data.keys()):
            if pfi.currency == "NGN":
                pfi.amount_ngn = pfi.amount
            elif pfi.exchange_rate and pfi.exchange_rate > 0:
                pfi.amount_ngn = pfi.amount * pfi.exchange_rate

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=pfi.operation_id,
            action="UPDATE_PFI",
            entity_type="pfi",
            entity_id=pfi.id,
            changes=changes,
            reason=data.reason,
        ))

        await db.commit()
        await db.refresh(pfi)
        await PfiService._attach_balance(pfi, db)
        return pfi

    # ── Volume drawdown / allocation ────────────────────────────────────────

    @staticmethod
    async def _attach_balance(pfi: PFI, db: AsyncSession) -> PFI:
        """Annotate transient allocated_litres/remaining_litres onto a PFI instance."""
        if pfi.quantity_litres is None:
            pfi.allocated_litres = Decimal("0")
            pfi.remaining_litres = None
            return pfi
        result = await db.execute(
            select(func.coalesce(func.sum(PfiAllocation.quantity_litres), 0))
            .where(PfiAllocation.pfi_id == pfi.id)
        )
        allocated = Decimal(result.scalar_one())
        pfi.allocated_litres = allocated
        pfi.remaining_litres = pfi.quantity_litres - allocated
        return pfi

    @staticmethod
    async def list_active_pfis(db: AsyncSession) -> List[PFI]:
        """PFIs with remaining allocatable volume — the BM's link-to-operation dropdown."""
        result = await db.execute(
            select(PFI)
            .where(
                PFI.status.notin_([PfiStatus.cancelled, PfiStatus.completed]),
                PFI.quantity_litres.isnot(None),
            )
            .order_by(PFI.created_at.desc())
        )
        pfis = list(result.scalars().all())
        active = []
        for pfi in pfis:
            await PfiService._attach_balance(pfi, db)
            if pfi.remaining_litres is not None and pfi.remaining_litres > 0:
                active.append(pfi)
        return active

    @staticmethod
    async def allocate_pfi_to_operation(
        pfi_id: UUID,
        operation_id: UUID,
        data: PfiAllocationCreate,
        current_user: User,
        db: AsyncSession,
    ) -> PfiAllocation:
        result = await db.execute(select(PFI).where(PFI.id == pfi_id))
        pfi = result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PFI not found")
        if pfi.quantity_litres is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PFI has no quantity set — cannot allocate",
            )

        operation = await _get_operation_or_404(operation_id, db)

        await PfiService._attach_balance(pfi, db)
        if data.quantity_litres > pfi.remaining_litres:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Only {pfi.remaining_litres} litres remain on PFI {pfi.pfi_number}",
            )

        allocation = PfiAllocation(
            pfi_id=pfi_id,
            operation_id=operation_id,
            quantity_litres=data.quantity_litres,
            linked_by=current_user.id,
            notes=data.notes,
        )
        db.add(allocation)
        await db.flush()

        if pfi.status in (PfiStatus.pending, PfiStatus.confirmed, PfiStatus.paid):
            pfi.status = PfiStatus.linked

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="ALLOCATE_PFI",
            entity_type="pfi_allocation",
            entity_id=allocation.id,
            changes={"pfi_number": pfi.pfi_number, "quantity_litres": str(data.quantity_litres)},
        ))

        await db.commit()
        await db.refresh(allocation)
        return allocation

    @staticmethod
    async def list_allocations_for_operation(operation_id: UUID, db: AsyncSession) -> List[PfiAllocation]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(PfiAllocation)
            .where(PfiAllocation.operation_id == operation_id)
            .order_by(PfiAllocation.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def _get_allocation_or_404(allocation_id: UUID, db: AsyncSession) -> PfiAllocation:
        result = await db.execute(select(PfiAllocation).where(PfiAllocation.id == allocation_id))
        allocation = result.scalar_one_or_none()
        if not allocation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allocation not found")
        return allocation

    @staticmethod
    async def update_allocation(
        allocation_id: UUID,
        data: PfiAllocationUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> PfiAllocation:
        allocation = await PfiService._get_allocation_or_404(allocation_id, db)

        pfi_result = await db.execute(select(PFI).where(PFI.id == allocation.pfi_id))
        pfi = pfi_result.scalar_one()

        others_result = await db.execute(
            select(func.coalesce(func.sum(PfiAllocation.quantity_litres), 0))
            .where(and_(PfiAllocation.pfi_id == pfi.id, PfiAllocation.id != allocation_id))
        )
        allocated_by_others = Decimal(others_result.scalar_one())
        remaining = pfi.quantity_litres - allocated_by_others
        if data.quantity_litres > remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Only {remaining} litres remain on PFI {pfi.pfi_number}",
            )

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        changes = capture_diff(allocation, update_data)

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=allocation.operation_id,
            action="UPDATE_PFI_ALLOCATION",
            entity_type="pfi_allocation",
            entity_id=allocation.id,
            changes=changes,
            reason=data.reason,
        ))

        await db.commit()
        await db.refresh(allocation)
        return allocation

    @staticmethod
    async def delete_allocation(
        allocation_id: UUID,
        reason: str,
        current_user: User,
        db: AsyncSession,
    ) -> None:
        allocation = await PfiService._get_allocation_or_404(allocation_id, db)

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=allocation.operation_id,
            action="DELETE_PFI_ALLOCATION",
            entity_type="pfi_allocation",
            entity_id=allocation.id,
            changes={"quantity_litres": str(allocation.quantity_litres)},
            reason=reason,
        ))

        await db.delete(allocation)
        await db.commit()


class PaymentService:

    @staticmethod
    async def record_payment(
        operation_id: UUID,
        data: PaymentCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Payment:
        operation = await _get_operation_or_404(operation_id, db)

        # Verify PFI belongs to this operation
        pfi_result = await db.execute(
            select(PFI).where(
                and_(PFI.id == data.pfi_id, PFI.operation_id == operation_id)
            )
        )
        pfi = pfi_result.scalar_one_or_none()
        if not pfi:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PFI not found on this operation",
            )

        voucher_number = await generate_voucher_number(db)

        payment = Payment(
            pfi_id=data.pfi_id,
            operation_id=operation_id,
            processed_by=current_user.id,
            amount=data.amount,
            currency=data.currency,
            payment_method=data.payment_method,
            payment_reference=data.payment_reference,
            payment_date=data.payment_date,
            voucher_number=voucher_number,
            voucher_url=data.voucher_url,
            notes=data.notes,
        )
        db.add(payment)

        # Update PFI status
        pfi.status = PfiStatus.payment_initiated

        await db.flush()

        # Transition operation to payment_processing
        await _transition_operation(
            operation, OperationStatus.payment_processing, current_user, db,
            reason=f"Payment voucher {voucher_number} recorded"
        )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="RECORD_PAYMENT",
            entity_type="payment",
            entity_id=payment.id,
            changes={"voucher_number": voucher_number, "amount": str(data.amount)},
        )
        db.add(audit)

        # Notify all Bunker Managers (in-app + WhatsApp)
        await _notify_role(
            db=db,
            role=UserRole.bunker_manager,
            operation_id=operation_id,
            type_="payment_update",
            title="Payment Recorded",
            message=f"Payment voucher {voucher_number} recorded for operation {operation.operation_number}. Awaiting confirmation.",
            wa_template="payment_confirmed",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "amount": str(data.amount),
                "currency": data.currency,
                "reference": voucher_number,
            },
        )

        await db.commit()
        await db.refresh(payment)
        return payment

    @staticmethod
    async def confirm_payment(
        operation_id: UUID,
        payment_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Payment:
        operation = await _get_operation_or_404(operation_id, db)

        payment_result = await db.execute(
            select(Payment).where(
                and_(Payment.id == payment_id, Payment.operation_id == operation_id)
            )
        )
        payment = payment_result.scalar_one_or_none()
        if not payment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

        # Mark PFI as paid
        pfi_result = await db.execute(select(PFI).where(PFI.id == payment.pfi_id))
        pfi = pfi_result.scalar_one_or_none()
        if pfi:
            pfi.status = PfiStatus.paid

        # Transition to payment_confirmed
        await _transition_operation(
            operation, OperationStatus.payment_confirmed, current_user, db,
            reason=f"Payment {payment.voucher_number} confirmed"
        )

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CONFIRM_PAYMENT",
            entity_type="payment",
            entity_id=payment.id,
            changes={"voucher_number": payment.voucher_number},
        )
        db.add(audit)

        # Notify all Ops Supervisors to proceed (in-app + WhatsApp)
        await _notify_role(
            db=db,
            role=UserRole.ops_supervisor,
            operation_id=operation_id,
            type_="milestone",
            title="Payment Confirmed — Proceed to Vessel Operations",
            message=f"Payment confirmed for operation {operation.operation_number}. Ready for vessel operations.",
            wa_template="vessel_task_assigned",
            wa_kwargs={"operation_number": operation.operation_number},
        )

        await db.commit()
        await db.refresh(payment)
        return payment

    @staticmethod
    async def list_payments(operation_id: UUID, db: AsyncSession) -> List[Payment]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(Payment)
            .where(Payment.operation_id == operation_id)
            .order_by(Payment.created_at.asc())
        )
        return list(result.scalars().all())
