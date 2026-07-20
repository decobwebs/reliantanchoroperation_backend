"""
Invoice service — Finance Manager generates invoices from approved BDNs.
Lifecycle: draft → sent → paid
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.models.finance import Invoice, Payment
from app.models.bdn import BDN
from app.models.operation import Operation
from app.models.user import User
from app.models.audit import AuditLog
from app.models.enums import BdnStatus, InvoiceStatus, OperationStatus, UserRole
from app.schemas.invoice import (
    InvoiceCreate,
    StandaloneInvoiceCreate,
    InvoiceSendRequest,
    InvoiceMarkPaidRequest,
)
from app.utils.number_generator import generate_invoice_number
from app.utils.invoice_pdf import generate_invoice_pdf
from app.services.email_service import email_pfi_linked
from app.services.document_service import _upload_to_supabase


class InvoiceService:

    @staticmethod
    async def _generate_and_upload_pdf(
        invoice: Invoice,
        operation: Optional[Operation],
        generated_by: User,
        db: AsyncSession,
    ) -> str:
        """Render + upload the invoice PDF.

        `operation` is None for standalone (ad-hoc) invoices: the client is then
        resolved from invoice.client_id, the operation panel is suppressed, and
        the line item comes from invoice.description.
        """
        bdn = None
        if invoice.bdn_id:
            bdn = await db.get(BDN, invoice.bdn_id)

        # Resolve who to bill, in order:
        #   operation-bound -> the operation's client (eager-loaded)
        #   standalone + registered client -> load that user
        #   standalone + manual client -> the free-text name/email on the invoice
        client = None
        if operation:
            client = operation.client
        elif invoice.client_id:
            client = await db.get(User, invoice.client_id)

        client_name = client.full_name if client else (invoice.client_name or "-")
        client_email = client.email if client else (invoice.client_email or "-")
        client_phone = client.phone if client else None

        pdf_bytes = generate_invoice_pdf(
            invoice_number=invoice.invoice_number,
            issue_date=invoice.created_at or datetime.utcnow(),
            due_date=invoice.due_date,
            operation_number=operation.operation_number if operation else None,
            operation_type=(
                (operation.type.value if hasattr(operation.type, "value") else str(operation.type))
                if operation else None
            ),
            operation_version=operation.version if operation else None,
            products=(
                [{"product_type": p.product_type, "quantity_mt": p.quantity_mt} for p in operation.products]
                if operation else None
            ),
            loading_location=operation.loading_location if operation else None,
            discharge_location=operation.discharge_location if operation else None,
            bdn_number=bdn.bdn_number if bdn else None,
            quantity_delivered_mt=(
                bdn.quantity_delivered_mt if bdn
                else (
                    (operation.actual_volume_mt or sum((p.quantity_mt for p in operation.products), Decimal("0")))
                    if operation else None
                )
            ),
            client_name=client_name,
            client_email=client_email,
            client_phone=client_phone,
            generated_by_name=generated_by.full_name,
            amount=invoice.amount,
            tax_amount=invoice.tax_amount,
            total_amount=invoice.total_amount,
            currency=invoice.currency,
            exchange_rate=invoice.exchange_rate,
            notes=invoice.notes,
            description=invoice.description,
        )
        # Standalone invoices have no operation id to key the path on.
        folder = str(operation.id) if operation else "standalone"
        storage_path = f"invoices/{folder}/{invoice.invoice_number}.pdf"
        return await _upload_to_supabase(pdf_bytes, storage_path, "application/pdf")

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
            select(Operation)
            .options(selectinload(Operation.client))
            .where(
                Operation.id == operation_id,
                Operation.deleted_at.is_(None),
            )
        )
        op = op_result.scalar_one_or_none()
        if not op:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        is_truck_only = op.type == OperationType.truck_only

        if is_truck_only:
            # Truck-only: invoice after delivery confirmed (pending_completion)
            # or directly from payment_confirmed (old compat flow).
            allowed_statuses = {
                OperationStatus.pending_completion,  # new path: delivery done → invoice
                OperationStatus.payment_confirmed,   # old compat
                OperationStatus.invoiced,
                OperationStatus.completed,
            }
            if op.status not in allowed_statuses:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Truck-only invoice requires delivery confirmation (pending_completion) "
                           f"or payment_confirmed. Current status: '{op.status.value}'.",
                )
            bdn_id = None
        else:
            # Vessel / full operation: BDN is required — Invoice is driven by actual delivery.
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
            # New path: invoice from bdn_approved (BDN drives invoice, payment was advance).
            # Old compat: invoice from payment_confirmed (pre-redesign flow).
            allowed_statuses = {
                OperationStatus.bdn_approved,        # new primary path
                OperationStatus.payment_confirmed,   # old compat
                OperationStatus.invoiced,
                OperationStatus.completed,
            }
            if op.status not in allowed_statuses:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot invoice an operation in '{op.status.value}' status. "
                           f"BDN must be approved first.",
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

        # Advance operation status to invoiced
        if op.status in (
            OperationStatus.bdn_approved,
            OperationStatus.pending_completion,
            OperationStatus.payment_confirmed,
        ):
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
        invoice.pdf_url = await InvoiceService._generate_and_upload_pdf(invoice, op, current_user, db)
        await db.flush()
        await db.refresh(invoice)
        return invoice

    @staticmethod
    async def create_standalone_invoice(
        data: StandaloneInvoiceCreate,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """Create an ad-hoc invoice with no operation (Finance-initiated billing).

        Deliberately skips everything the operation-bound path enforces — BDN
        requirement, operation status gates, and the operation -> 'invoiced'
        status side-effect — because none of them apply without an operation.
        """
        # A registered client must exist and actually be a client. A manual client
        # (free-text name) is accepted as-is — that's the point of the escape hatch.
        if data.client_id:
            client = await db.get(User, data.client_id)
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client not found",
                )
            if client.role != UserRole.client:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invoices can only be billed to a client user",
                )

        total = data.amount + data.tax_amount
        invoice_number = await generate_invoice_number(db)

        invoice = Invoice(
            invoice_number=invoice_number,
            operation_id=None,
            bdn_id=None,
            client_id=data.client_id,
            client_name=data.client_name,
            client_email=data.client_email,
            generated_by=current_user.id,
            amount=data.amount,
            currency=data.currency,
            exchange_rate=data.exchange_rate,
            tax_amount=data.tax_amount,
            total_amount=total,
            due_date=data.due_date,
            status=InvoiceStatus.draft,
            description=data.description,
            notes=data.notes,
        )
        db.add(invoice)

        # audit_logs.operation_id is nullable, so a null operation is fine here.
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=None,
            action="CREATE_STANDALONE_INVOICE",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={
                "invoice_number": invoice_number,
                "amount": str(data.amount),
                "currency": data.currency,
                "client_id": str(data.client_id) if data.client_id else None,
                "client_name": data.client_name,
                "description": data.description,
            },
        ))

        await db.flush()
        invoice.pdf_url = await InvoiceService._generate_and_upload_pdf(invoice, None, current_user, db)
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
        elif not invoice.pdf_url:
            op = None
            if invoice.operation_id:
                op_result = await db.execute(
                    select(Operation)
                    .options(selectinload(Operation.client))
                    .where(Operation.id == invoice.operation_id)
                )
                op = op_result.scalar_one_or_none()
            # Standalone invoices (op is None) still render a PDF.
            if op or invoice.operation_id is None:
                invoice.pdf_url = await InvoiceService._generate_and_upload_pdf(invoice, op, current_user, db)
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
    async def generate_pdf(
        invoice_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Invoice:
        """Generate or replace the stored PDF for an existing invoice."""
        invoice = await InvoiceService._get_invoice(invoice_id, db)
        if invoice.status == InvoiceStatus.cancelled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot generate a PDF for a cancelled invoice",
            )

        # Standalone invoices have no operation — that's valid, not a 404.
        op = None
        if invoice.operation_id:
            op_result = await db.execute(
                select(Operation)
                .options(selectinload(Operation.client))
                .where(Operation.id == invoice.operation_id)
            )
            op = op_result.scalar_one_or_none()
            if not op:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        invoice.pdf_url = await InvoiceService._generate_and_upload_pdf(invoice, op, current_user, db)
        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=invoice.operation_id,
            action="GENERATE_INVOICE_PDF",
            entity_type="invoice",
            entity_id=invoice.id,
            changes={"invoice_number": invoice.invoice_number, "pdf_url": invoice.pdf_url},
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

        # Transition operation to completed if still in invoiced state.
        # Standalone invoices have no operation to advance — skip entirely.
        if invoice.operation_id:
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
    async def advance_paid_for_operation(operation_id: Optional[UUID], db: AsyncSession) -> Decimal:
        """Sum of all advance payments recorded for this operation (via PFI).

        Standalone invoices have no operation, so there are no advance payments
        to reconcile against — return zero rather than querying on NULL.
        """
        if operation_id is None:
            return Decimal("0")
        result = await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.operation_id == operation_id
            )
        )
        return Decimal(str(result.scalar_one()))

    @staticmethod
    async def list_invoices(operation_id: UUID, db: AsyncSession) -> list:
        result = await db.execute(
            select(Invoice)
            .where(Invoice.operation_id == operation_id)
            .order_by(Invoice.created_at.desc())
        )
        return result.scalars().all()

    @staticmethod
    async def list_all_invoices(
        db: AsyncSession,
        status_filter: Optional[str] = None,
        standalone_only: bool = False,
        limit: int = 200,
    ) -> list:
        """List invoices across the whole system (Finance overview).

        Bounded: this grows forever otherwise, and each row is signed + reconciled
        by the router. Newest first, so the cap drops the oldest.
        """
        stmt = select(Invoice)
        if standalone_only:
            stmt = stmt.where(Invoice.operation_id.is_(None))
        if status_filter:
            try:
                stmt = stmt.where(Invoice.status == InvoiceStatus(status_filter))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid invoice status '{status_filter}'",
                )
        result = await db.execute(
            stmt.order_by(Invoice.created_at.desc()).limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def _get_invoice(invoice_id: UUID, db: AsyncSession) -> Invoice:
        result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
        invoice = result.scalar_one_or_none()
        if not invoice:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
        return invoice
