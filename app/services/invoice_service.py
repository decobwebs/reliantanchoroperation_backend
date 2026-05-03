"""
Invoice service — Finance Manager generates invoices from approved BDNs.
Lifecycle: draft → sent → paid
"""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.finance import Invoice
from app.models.bdn import BDN
from app.models.operation import Operation
from app.models.user import User
from app.models.audit import AuditLog
from app.models.enums import BdnStatus, InvoiceStatus, OperationStatus
from app.schemas.invoice import InvoiceCreate, InvoiceSendRequest, InvoiceMarkPaidRequest
from app.utils.number_generator import generate_invoice_number
from app.services.email_service import email_pfi_linked


class InvoiceService:

    @staticmethod
    async def create_invoice(
        operation_id: UUID,
        data: InvoiceCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """
        Generate an invoice for an operation.
        - Vessel / full operations: requires an approved BDN (bdn_id must be provided).
        - Truck-only operations: no BDN required; operation must be in payment_confirmed.
        """
        from app.models.enums import OperationType

        op_result = await db.execute(
            select(Operation).where(
                Operation.id == operation_id,
                Operation.deleted_at.is_(None),
            )
        )
        op = op_result.scalar_one_or_none()
        if not op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        is_truck_only = op.type == OperationType.truck_only

        if is_truck_only:
            # Truck-only: invoice on payment_confirmed (no BDN)
            allowed_statuses = {
                OperationStatus.payment_confirmed,
                OperationStatus.invoiced,
                OperationStatus.completed,
            }
            if op.status not in allowed_statuses:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Truck-only invoice requires payment_confirmed status. "
                           f"Current status: '{op.status.value}'.",
                )
            bdn_id = None
        else:
            # Vessel / full operation: BDN is required
            if not data.bdn_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="bdn_id is required for vessel or full operations.",
                )
            bdn_result = await db.execute(
                select(BDN).where(
                    BDN.id == data.bdn_id,
                    BDN.operation_id == operation_id,
                    BDN.status == BdnStatus.approved,
                )
            )
            bdn = bdn_result.scalar_one_or_none()
            if not bdn:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Approved BDN not found for this operation",
                )
            allowed_statuses = {
                OperationStatus.bdn_approved,
                OperationStatus.invoiced,
                OperationStatus.completed,
            }
            if op.status not in allowed_statuses:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot invoice an operation in '{op.status.value}' status. "
                           f"Operation must be in bdn_approved or later.",
                )
            # Prevent duplicate invoices for the same BDN
            existing = await db.execute(
                select(Invoice).where(Invoice.bdn_id == data.bdn_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An invoice already exists for this BDN",
                )
            bdn_id = data.bdn_id

        total = data.amount + data.tax_amount
        invoice_number = await generate_invoice_number(db)

        invoice = Invoice(
            invoice_number=invoice_number,
            operation_id=operation_id,
            bdn_id=bdn_id,
            client_id=op.client_id,
            generated_by=current_user.id,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            tax_amount=data.tax_amount,
            total_amount=total,
            due_date=data.due_date,
            status=InvoiceStatus.draft,
            notes=data.notes,
        )
        db.add(invoice)

        # Advance operation status
        if op.status in (OperationStatus.bdn_approved, OperationStatus.payment_confirmed):
            op.status = OperationStatus.invoiced
            op.updated_at = datetime.utcnow()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_INVOICE",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={
                "invoice_number": invoice_number,
                "amount": str(data.amount),
                "currency": data.currency,
                "bdn_id": str(bdn_id) if bdn_id else None,
            },
        ))

        await db.flush()
        await db.refresh(invoice)
        return invoice

    @staticmethod
    async def send_invoice(
        invoice_id: UUID,
        data: InvoiceSendRequest,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """Mark invoice as sent and optionally attach a PDF URL."""
        invoice = await InvoiceService._get_invoice(invoice_id, db)

        if invoice.status != InvoiceStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invoice is already in '{invoice.status.value}' status",
            )

        invoice.status = InvoiceStatus.sent
        invoice.sent_at = datetime.utcnow()
        if data.pdf_url:
            invoice.pdf_url = data.pdf_url
        if data.notes:
            invoice.notes = data.notes

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=invoice.operation_id,
            action="SEND_INVOICE",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={"invoice_number": invoice.invoice_number, "pdf_url": data.pdf_url},
        ))

        await db.flush()
        await db.refresh(invoice)
        return invoice

    @staticmethod
    async def mark_paid(
        invoice_id: UUID,
        data: InvoiceMarkPaidRequest,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """Mark invoice as paid."""
        invoice = await InvoiceService._get_invoice(invoice_id, db)

        if invoice.status not in {InvoiceStatus.sent, InvoiceStatus.overdue}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Can only mark sent or overdue invoices as paid, not '{invoice.status.value}'",
            )

        invoice.status = InvoiceStatus.paid
        invoice.paid_at = datetime.utcnow()
        if data.notes:
            invoice.notes = data.notes

        # Transition operation to completed if still in invoiced state
        op_result = await db.execute(
            select(Operation).where(Operation.id == invoice.operation_id)
        )
        op = op_result.scalar_one_or_none()
        if op and op.status == OperationStatus.invoiced:
            op.status = OperationStatus.completed
            op.completed_at = datetime.utcnow()
            op.updated_at = datetime.utcnow()

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=invoice.operation_id,
            action="MARK_INVOICE_PAID",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={"invoice_number": invoice.invoice_number},
        ))

        await db.flush()
        await db.refresh(invoice)
        return invoice

    @staticmethod
    async def cancel_invoice(
        invoice_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """Cancel an invoice (draft or sent only)."""
        invoice = await InvoiceService._get_invoice(invoice_id, db)

        if invoice.status in {InvoiceStatus.paid, InvoiceStatus.cancelled}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot cancel a '{invoice.status.value}' invoice",
            )

        invoice.status = InvoiceStatus.cancelled

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=invoice.operation_id,
            action="CANCEL_INVOICE",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={"invoice_number": invoice.invoice_number},
        ))

        await db.flush()
        await db.refresh(invoice)
        return invoice

    @staticmethod
    async def list_invoices(operation_id: UUID, db: AsyncSession) -> list:
        result = await db.execute(
            select(Invoice)
            .where(Invoice.operation_id == operation_id)
            .order_by(Invoice.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def _get_invoice(invoice_id: UUID, db: AsyncSession) -> Invoice:
        result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        invoice = result.scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        return invoice
