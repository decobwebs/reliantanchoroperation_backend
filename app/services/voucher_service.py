"""
Voucher service — tracks outgoing expenses (disbursements) per operation.
Lifecycle: draft → submitted → approved / rejected
"""
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models.finance import Voucher
from app.models.operation import Operation
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import VoucherStatus, UserRole
from app.schemas.voucher import VoucherCreate, VoucherApproveRequest, VoucherRejectRequest
from app.utils.number_generator import generate_expense_voucher_number


class VoucherService:

    @staticmethod
    async def create_voucher(
        operation_id: UUID,
        data: VoucherCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Voucher:
        op = await db.get(Operation, operation_id)
        if not op or op.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        voucher_number = await generate_expense_voucher_number(db)

        amount_ngn = None
        if data.currency == "NGN":
            amount_ngn = data.amount
        elif data.exchange_rate and data.exchange_rate > 0:
            amount_ngn = data.amount * data.exchange_rate

        voucher = Voucher(
            voucher_number=voucher_number,
            operation_id=operation_id,
            pfi_id=data.pfi_id,
            recorded_by=current_user.id,
            category=data.category,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            amount_ngn=amount_ngn,
            supplier_name=data.supplier_name,
            description=data.description,
            payment_date=data.payment_date,
            notes=data.notes,
            status=VoucherStatus.draft,
        )
        db.add(voucher)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_VOUCHER",
            entity_type="voucher",
            entity_id=voucher.id,
            changes={
                "voucher_number": voucher_number,
                "category": data.category.value,
                "amount": str(data.amount),
                "currency": data.currency,
            },
        ))

        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def create_standalone_voucher(
        data: VoucherCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Voucher:
        """Create a voucher not tied to a specific operation."""
        voucher_number = await generate_expense_voucher_number(db)

        amount_ngn = None
        if data.currency == "NGN":
            amount_ngn = data.amount
        elif data.exchange_rate and data.exchange_rate > 0:
            amount_ngn = data.amount * data.exchange_rate

        voucher = Voucher(
            voucher_number=voucher_number,
            operation_id=None,
            pfi_id=data.pfi_id,
            recorded_by=current_user.id,
            category=data.category,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            amount_ngn=amount_ngn,
            supplier_name=data.supplier_name,
            description=data.description,
            payment_date=data.payment_date,
            notes=data.notes,
            status=VoucherStatus.draft,
        )
        db.add(voucher)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=None,
            action="CREATE_VOUCHER",
            entity_type="voucher",
            entity_id=voucher.id,
            changes={"voucher_number": voucher_number, "category": data.category.value, "amount": str(data.amount)},
        ))

        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def submit_voucher(voucher_id: UUID, current_user: User, db: AsyncSession) -> Voucher:
        voucher = await VoucherService._get_or_404(voucher_id, db)
        if voucher.recorded_by != current_user.id and current_user.role != UserRole.bunker_manager:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorised")
        if voucher.status != VoucherStatus.draft:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Voucher is already '{voucher.status.value}'")
        voucher.status = VoucherStatus.submitted
        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=voucher.operation_id,
            action="SUBMIT_VOUCHER", entity_type="voucher", entity_id=voucher.id,
            changes={"voucher_number": voucher.voucher_number},
        ))
        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def approve_voucher(
        voucher_id: UUID, data: VoucherApproveRequest, current_user: User, db: AsyncSession
    ) -> Voucher:
        voucher = await VoucherService._get_or_404(voucher_id, db)
        if voucher.status != VoucherStatus.submitted:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Can only approve submitted vouchers, not '{voucher.status.value}'")
        voucher.status = VoucherStatus.approved
        voucher.approved_by = current_user.id
        voucher.approved_at = datetime.utcnow()
        if data.notes:
            voucher.notes = data.notes
        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=voucher.operation_id,
            action="APPROVE_VOUCHER", entity_type="voucher", entity_id=voucher.id,
            changes={"voucher_number": voucher.voucher_number},
        ))
        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def reject_voucher(
        voucher_id: UUID, data: VoucherRejectRequest, current_user: User, db: AsyncSession
    ) -> Voucher:
        voucher = await VoucherService._get_or_404(voucher_id, db)
        if voucher.status != VoucherStatus.submitted:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Can only reject submitted vouchers")
        voucher.status = VoucherStatus.rejected
        voucher.rejection_reason = data.reason
        await db.flush()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=voucher.operation_id,
            action="REJECT_VOUCHER", entity_type="voucher", entity_id=voucher.id,
            changes={"voucher_number": voucher.voucher_number, "reason": data.reason},
        ))
        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def attach_receipt(
        voucher_id: UUID, receipt_url: str, current_user: User, db: AsyncSession
    ) -> Voucher:
        voucher = await VoucherService._get_or_404(voucher_id, db)
        voucher.receipt_url = receipt_url
        await db.flush()
        await db.commit()
        await db.refresh(voucher)
        return voucher

    @staticmethod
    async def list_by_operation(operation_id: UUID, db: AsyncSession) -> List[Voucher]:
        result = await db.execute(
            select(Voucher)
            .where(Voucher.operation_id == operation_id)
            .order_by(Voucher.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        db: AsyncSession,
        status_filter: Optional[str] = None,
        operation_id: Optional[UUID] = None,
    ) -> List[Voucher]:
        stmt = select(Voucher).order_by(Voucher.created_at.desc())
        if status_filter:
            stmt = stmt.where(Voucher.status == status_filter)
        if operation_id:
            stmt = stmt.where(Voucher.operation_id == operation_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _get_or_404(voucher_id: UUID, db: AsyncSession) -> Voucher:
        result = await db.execute(select(Voucher).where(Voucher.id == voucher_id))
        v = result.scalar_one_or_none()
        if not v:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found")
        return v
