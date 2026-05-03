"""
Invoice endpoints — Finance Manager generates and manages invoices.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.invoice import InvoiceCreate, InvoiceSendRequest, InvoiceMarkPaidRequest, InvoiceOut
from app.services.invoice_service import InvoiceService

router = APIRouter(tags=["Invoices"])

_fm_only = Depends(require_roles(UserRole.finance_manager))
_fm_bm = Depends(require_roles(UserRole.finance_manager, UserRole.bunker_manager))


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
        data=InvoiceOut.model_validate(invoice).model_dump(),
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
    items = [InvoiceOut.model_validate(inv).model_dump() for inv in invoices]
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
        data=InvoiceOut.model_validate(invoice).model_dump(),
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
        data=InvoiceOut.model_validate(invoice).model_dump(),
        message="Invoice marked as sent",
    )


@router.post("/invoices/{invoice_id}/mark-paid", response_model=StandardResponse)
async def mark_invoice_paid(
    invoice_id: UUID,
    body: InvoiceMarkPaidRequest,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Mark invoice as paid. Transitions operation to completed. FM only."""
    invoice = await InvoiceService.mark_paid(invoice_id, body, current_user, db)
    return StandardResponse.ok(
        data=InvoiceOut.model_validate(invoice).model_dump(),
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
        data=InvoiceOut.model_validate(invoice).model_dump(),
        message="Invoice cancelled",
    )
