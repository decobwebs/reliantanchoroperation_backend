"""
Voucher endpoints — track outgoing expenses / disbursements.
FM records vouchers; BM approves or rejects them.
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
from app.schemas.voucher import (
    VoucherCreate,
    VoucherBulkCreate,
    VoucherApproveRequest,
    VoucherRejectRequest,
    VoucherAttachReceiptRequest,
    VoucherOut,
)
from app.services.voucher_service import VoucherService

router = APIRouter(tags=["Vouchers"])

_fm_only = Depends(require_roles(UserRole.finance_manager))
_bm_only = Depends(require_roles(UserRole.bunker_manager))
_fm_bm = Depends(require_roles(UserRole.finance_manager, UserRole.bunker_manager))


# ── Operation-scoped vouchers ──────────────────────────────────────────────────

@router.post(
    "/operations/{operation_id}/vouchers",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_voucher(
    operation_id: UUID,
    body: VoucherCreate,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Record an expense voucher against an operation. Finance Manager only."""
    voucher = await VoucherService.create_voucher(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message=f"Voucher {voucher.voucher_number} recorded",
    )


@router.post(
    "/operations/{operation_id}/vouchers/bulk",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_vouchers_bulk(
    operation_id: UUID,
    body: VoucherBulkCreate,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Record multiple expense vouchers against an operation. Finance Manager only."""
    vouchers = await VoucherService.create_vouchers(operation_id, body.vouchers, current_user, db)
    items = [VoucherOut.model_validate(v).model_dump() for v in vouchers]
    return StandardResponse.ok(
        data=items,
        message=f"{len(items)} voucher{'s' if len(items) != 1 else ''} recorded",
    )


@router.get("/operations/{operation_id}/vouchers", response_model=StandardResponse)
async def list_operation_vouchers(
    operation_id: UUID,
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """List all vouchers for an operation. FM and BM."""
    vouchers = await VoucherService.list_by_operation(operation_id, db)
    items = [VoucherOut.model_validate(v).model_dump() for v in vouchers]
    return StandardResponse.ok(data=items, message="Vouchers retrieved")


# ── Standalone (non-operation) vouchers ───────────────────────────────────────

@router.post(
    "/vouchers",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_standalone_voucher(
    body: VoucherCreate,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Record an expense voucher not tied to a specific operation. Finance Manager only."""
    voucher = await VoucherService.create_standalone_voucher(body, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message=f"Voucher {voucher.voucher_number} recorded",
    )


@router.get("/vouchers", response_model=StandardResponse)
async def list_all_vouchers(
    status_filter: Optional[str] = Query(None, alias="status"),
    operation_id: Optional[UUID] = Query(None),
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """List all vouchers with optional filters. FM and BM."""
    vouchers = await VoucherService.list_all(db, status_filter=status_filter, operation_id=operation_id)
    items = [VoucherOut.model_validate(v).model_dump() for v in vouchers]
    return StandardResponse.ok(data=items, message="Vouchers retrieved")


# ── Voucher lifecycle ──────────────────────────────────────────────────────────

@router.post("/vouchers/{voucher_id}/submit", response_model=StandardResponse)
async def submit_voucher(
    voucher_id: UUID,
    current_user: User = _fm_only,
    db: AsyncSession = Depends(get_db),
):
    """Submit a draft voucher for approval. Finance Manager only."""
    voucher = await VoucherService.submit_voucher(voucher_id, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message="Voucher submitted for approval",
    )


@router.post("/vouchers/{voucher_id}/approve", response_model=StandardResponse)
async def approve_voucher(
    voucher_id: UUID,
    body: VoucherApproveRequest = VoucherApproveRequest(),
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Approve a submitted voucher. Bunker Manager only."""
    voucher = await VoucherService.approve_voucher(voucher_id, body, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message="Voucher approved",
    )


@router.post("/vouchers/{voucher_id}/reject", response_model=StandardResponse)
async def reject_voucher(
    voucher_id: UUID,
    body: VoucherRejectRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Reject a submitted voucher with a reason. Bunker Manager only."""
    voucher = await VoucherService.reject_voucher(voucher_id, body, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message="Voucher rejected",
    )


@router.post("/vouchers/{voucher_id}/receipt", response_model=StandardResponse)
async def attach_receipt(
    voucher_id: UUID,
    body: VoucherAttachReceiptRequest,
    current_user: User = _fm_bm,
    db: AsyncSession = Depends(get_db),
):
    """Attach a receipt URL to a voucher. FM and BM."""
    voucher = await VoucherService.attach_receipt(voucher_id, body.receipt_url, current_user, db)
    return StandardResponse.ok(
        data=VoucherOut.model_validate(voucher).model_dump(),
        message="Receipt attached",
    )
