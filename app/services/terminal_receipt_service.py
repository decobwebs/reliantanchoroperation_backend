from typing import List
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.bdn import TerminalLoadingReceipt
from app.models.operation import Operation
from app.models.truck import TruckOperation
from app.models.user import User
from app.models.enums import TruckOpStatus, VesselSourceType
from app.models.audit import AuditLog
from app.schemas.terminal_receipt import TerminalLoadingReceiptCreate, QuantitySummaryOut


async def _get_terminal_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    if operation.source_type != VesselSourceType.terminal:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Terminal loading receipts only apply to terminal-sourced operations",
        )
    return operation


class TerminalReceiptService:

    @staticmethod
    async def create_receipt(
        operation_id: UUID, data: TerminalLoadingReceiptCreate, current_user: User, db: AsyncSession,
    ) -> TerminalLoadingReceipt:
        await _get_terminal_operation_or_404(operation_id, db)

        receipt = TerminalLoadingReceipt(
            operation_id=operation_id,
            quantity_litres=data.quantity_litres,
            gov=data.gov,
            gsv=data.gsv,
            mt_vacuum=data.mt_vacuum,
            description=data.description,
            recorded_by=current_user.id,
        )
        db.add(receipt)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation_id, action="CREATE_TERMINAL_LOADING_RECEIPT",
            entity_type="terminal_loading_receipt", entity_id=receipt.id,
            changes={"quantity_litres": str(data.quantity_litres), "mt_vacuum": str(data.mt_vacuum) if data.mt_vacuum is not None else None},
        ))

        await db.flush()
        await db.refresh(receipt)
        receipt._recorded_by_name = current_user.full_name
        return receipt

    @staticmethod
    async def list_receipts(operation_id: UUID, db: AsyncSession) -> List[TerminalLoadingReceipt]:
        await _get_terminal_operation_or_404(operation_id, db)
        result = await db.execute(
            select(TerminalLoadingReceipt)
            .where(TerminalLoadingReceipt.operation_id == operation_id)
            .options(selectinload(TerminalLoadingReceipt.recorder))
            .order_by(TerminalLoadingReceipt.recorded_at.desc())
        )
        receipts = list(result.scalars().all())
        for r in receipts:
            r._recorded_by_name = r.recorder.full_name if r.recorder else None
        return receipts

    @staticmethod
    async def quantity_summary(operation_id: UUID, db: AsyncSession) -> QuantitySummaryOut:
        """Total Loaded Quantity — truck deliveries (quantity_loaded_mt,
        excluding cancelled truck ops) + terminal receipts (mt_vacuum, where
        recorded). Applies to any operation type; each side is simply zero
        where that source doesn't apply."""
        operation = await db.execute(
            select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        if not operation.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        truck_result = await db.execute(
            select(func.coalesce(func.sum(TruckOperation.quantity_loaded_mt), 0)).where(
                and_(
                    TruckOperation.operation_id == operation_id,
                    TruckOperation.status != TruckOpStatus.cancelled,
                )
            )
        )
        truck_loaded_mt = Decimal(truck_result.scalar() or 0)

        terminal_result = await db.execute(
            select(func.coalesce(func.sum(TerminalLoadingReceipt.mt_vacuum), 0)).where(
                TerminalLoadingReceipt.operation_id == operation_id
            )
        )
        terminal_loaded_mt = Decimal(terminal_result.scalar() or 0)

        return QuantitySummaryOut(
            truck_loaded_mt=truck_loaded_mt,
            terminal_loaded_mt=terminal_loaded_mt,
            total_loaded_mt=truck_loaded_mt + terminal_loaded_mt,
        )
