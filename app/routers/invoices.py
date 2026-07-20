"""
Invoice endpoints — Finance Manager generates and manages invoices.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.invoice import (
    InvoiceCreate,
    StandaloneInvoiceCreate,
    InvoiceSendRequest,
    InvoiceMarkPaidRequest,
    InvoiceOut,
)
from app.services.document_service import create_signed_supabase_url
from app.services.invoice_service import InvoiceService

router = APIRouter(tags=["Invoices"])

_fm_only = Depends(require_roles(UserRole.finance_manager))
_fm_bm = Depends(require_roles(UserRole.finance_manager, UserRole.bunker_manager))
# Financial controls carve-out — marking an invoice paid is the finalization
# moment, so it stays gated to the real FM even when BM is acting-as-FM.
_fm_only_strict = Depends(require_roles(UserRole.finance_manager, allow_acting_as=False))


async def _serialize_invoice(invoice, db: AsyncSession = None) -> dict:
    item = InvoiceOut.model_validate(invoice).model_dump()
    item["pdf_url"] = await create_signed_supabase_url(item.get("pdf_url"))
    if db is not None:
        advance = await InvoiceService.advance_paid_for_operation(invoice.operation_id, db)
        item["advance_paid"] = advance
        item["balance_due"] = invoice.total_amount - advance
    return item


@router.post("/operations/{operation_id}/invoices", response_model=StandardResponse, status_code=201)
async def create_invoice(
    operation_id: UUID,
    body: InvoiceCreate,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Generate an invoice from an approved BDN. Finance Manager only."""
    invoice = await InvoiceService.create_invoice(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message=f"Invoice {invoice.invoice_number} created",
    )


@router.get("/operations/{operation_id}/invoices", response_model=StandardResponse)
async def list_invoices(
    operation_id: UUID,
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """List all invoices for an operation. FM and BM."""
    invoices = await InvoiceService.list_invoices(operation_id, db)
    items = [await _serialize_invoice(inv, db) for inv in invoices]
    return StandardResponse.ok(data=items, message="Invoices retrieved")


@router.post("/invoices", response_model=StandardResponse, status_code=201)
async def create_standalone_invoice(
    body: StandaloneInvoiceCreate,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Create an ad-hoc invoice not tied to any operation. Finance Manager only.

    Unlike the operation-scoped route this has no BDN requirement and does not
    touch any operation's status.
    """
    invoice = await InvoiceService.create_standalone_invoice(body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message=f"Invoice {invoice.invoice_number} created",
    )


# NOTE: must stay ABOVE /invoices/{invoice_id} or the param route shadows it.
@router.get("/invoices", response_model=StandardResponse)
async def list_all_invoices(
    status_filter: Optional[str] = Query(None, alias="status"),
    standalone_only: bool = Query(False),
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """List invoices across all operations, incl. standalone ones. FM and BM."""
    invoices = await InvoiceService.list_all_invoices(
        db, status_filter=status_filter, standalone_only=standalone_only
    )
    items = [await _serialize_invoice(inv, db) for inv in invoices]
    return StandardResponse.ok(data=items, message="Invoices retrieved")


@router.get("/invoices/{invoice_id}", response_model=StandardResponse)
async def get_invoice(
    invoice_id: UUID,
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """Get a single invoice by ID. FM and BM."""
    invoice = await InvoiceService._get_invoice(invoice_id, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message="Invoice retrieved",
    )


@router.post("/invoices/{invoice_id}/send", response_model=StandardResponse)
async def send_invoice(
    invoice_id: UUID,
    body: InvoiceSendRequest,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Mark invoice as sent (attach PDF URL if available). FM only."""
    invoice = await InvoiceService.send_invoice(invoice_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message="Invoice marked as sent",
    )


@router.post("/invoices/{invoice_id}/generate-pdf", response_model=StandardResponse)
async def generate_invoice_pdf(
    invoice_id: UUID,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Generate or replace the professional invoice PDF. FM only."""
    invoice = await InvoiceService.generate_pdf(invoice_id, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message="Invoice PDF generated",
    )


@router.post("/invoices/{invoice_id}/mark-paid", response_model=StandardResponse)
async def mark_invoice_paid(
    invoice_id: UUID,
    body: InvoiceMarkPaidRequest,
    current_user: User = _fm_only_strict,
    db: AsyncSession = Depends(get_db),
):
    """Mark invoice as paid. Transitions operation to completed. Real Finance
    Manager only (financial controls carve-out — no acting-as bypass)."""
    invoice = await InvoiceService.mark_paid(invoice_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message="Invoice marked as paid",
    )


@router.post("/invoices/{invoice_id}/cancel", response_model=StandardResponse)
async def cancel_invoice(
    invoice_id: UUID,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a draft or sent invoice. FM only."""
    invoice = await InvoiceService.cancel_invoice(invoice_id, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_invoice(invoice, db),
        message="Invoice cancelled",
    )
