from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.pfi import (
    PfiCreate, PfiGenerateRequest, PfiOut,
    PaymentCreate, PaymentOut, PaymentConfirmRequest,
    StandalonePfiCreate, PfiConfirmPaymentRequest,
    PfiAllocationCreate, PfiAllocationUpdate, PfiAllocationOut,
)
from app.services.document_service import create_signed_supabase_url
from app.services.pfi_service import PfiService, PaymentService

router = APIRouter(tags=["Finance"])


# ── PFI endpoints ──────────────────────────────────────────────────────────────

async def _serialize_pfi(pfi) -> dict:
    item = PfiOut.model_validate(pfi).model_dump()
    item["document_url"] = await create_signed_supabase_url(item.get("document_url"))
    item["receipt_url"] = await create_signed_supabase_url(item.get("receipt_url"))
    return item


@router.post(
    "/operations/{operation_id}/pfis/generate",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_pfi(
    operation_id: UUID,
    body: PfiGenerateRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a PFI PDF from operation data and link it. BM or Finance Manager."""
    pfi = await PfiService.generate_pfi(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_pfi(pfi),
        message=f"PFI {pfi.pfi_number} generated and linked to operation",
    )


@router.post(
    "/operations/{operation_id}/pfis",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pfi(
    operation_id: UUID,
    body: PfiCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Link a PFI to an operation and advance its status. BM or Finance Manager."""
    pfi = await PfiService.create_pfi(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_pfi(pfi),
        message=f"PFI {pfi.pfi_number} linked to operation",
    )


@router.get("/operations/{operation_id}/pfis", response_model=StandardResponse)
async def list_pfis(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all PFIs for an operation. BM and Finance Manager only."""
    pfis = await PfiService.list_pfis(operation_id, db)
    items = [await _serialize_pfi(p) for p in pfis]
    return StandardResponse.ok(data=items, message="PFIs retrieved")


# NOTE: /pfis/active must be registered before /pfis/{pfi_id} — FastAPI matches
# routes in registration order, so a static path declared after a dynamic one
# with the same shape gets shadowed (here, "active" would be parsed as pfi_id
# and fail UUID validation).
@router.get("/pfis/active", response_model=StandardResponse)
async def list_active_pfis(
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """PFIs with remaining volume — the BM's link-to-operation dropdown source."""
    pfis = await PfiService.list_active_pfis(db)
    items = [await _serialize_pfi(p) for p in pfis]
    return StandardResponse.ok(data=items, message="Active PFIs retrieved")


@router.get("/pfis/{pfi_id}", response_model=StandardResponse)
async def get_pfi(
    pfi_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get a single PFI by ID. BM and Finance Manager only."""
    pfi = await PfiService.get_pfi(pfi_id, db)
    return StandardResponse.ok(
        data=await _serialize_pfi(pfi),
        message="PFI retrieved",
    )


# ── PFI Allocation (volume drawdown against an operation) ──────────────────────

@router.post(
    "/operations/{operation_id}/pfis/{pfi_id}/allocations",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def allocate_pfi(
    operation_id: UUID,
    pfi_id: UUID,
    body: PfiAllocationCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """BM allocates a fixed quantity of a PFI to this operation. Bunker Manager only."""
    allocation = await PfiService.allocate_pfi_to_operation(pfi_id, operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=PfiAllocationOut.model_validate(allocation).model_dump(),
        message="PFI allocated to operation",
    )


@router.get("/operations/{operation_id}/pfis/allocations", response_model=StandardResponse)
async def list_pfi_allocations(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List PFI allocations for an operation. BM and Finance Manager."""
    allocations = await PfiService.list_allocations_for_operation(operation_id, db)
    items = [PfiAllocationOut.model_validate(a).model_dump() for a in allocations]
    return StandardResponse.ok(data=items, message="Allocations retrieved")


@router.put("/pfi-allocations/{allocation_id}", response_model=StandardResponse)
async def update_pfi_allocation(
    allocation_id: UUID,
    body: PfiAllocationUpdate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Edit an allocation's quantity. Requires a reason. Bunker Manager only."""
    allocation = await PfiService.update_allocation(allocation_id, body, current_user, db)
    return StandardResponse.ok(
        data=PfiAllocationOut.model_validate(allocation).model_dump(),
        message="Allocation updated",
    )


@router.delete("/pfi-allocations/{allocation_id}", response_model=StandardResponse)
async def delete_pfi_allocation(
    allocation_id: UUID,
    reason: str = Query(..., min_length=1),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Delete an allocation. Requires a reason. Bunker Manager only."""
    await PfiService.delete_allocation(allocation_id, reason, current_user, db)
    return StandardResponse.ok(data=None, message="Allocation deleted")


# ── Payment endpoints ──────────────────────────────────────────────────────────

@router.post(
    "/operations/{operation_id}/payments",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment(
    operation_id: UUID,
    body: PaymentCreate,
    current_user: User = Depends(require_roles(UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record a payment against a PFI. Finance Manager only."""
    payment = await PaymentService.record_payment(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=PaymentOut.model_validate(payment).model_dump(),
        message=f"Payment voucher {payment.voucher_number} recorded",
    )


@router.post(
    "/operations/{operation_id}/payments/{payment_id}/confirm",
    response_model=StandardResponse,
)
async def confirm_payment(
    operation_id: UUID,
    payment_id: UUID,
    body: PaymentConfirmRequest = PaymentConfirmRequest(),
    current_user: User = Depends(require_roles(UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a payment, advancing operation to payment_confirmed. Finance Manager only."""
    payment = await PaymentService.confirm_payment(operation_id, payment_id, current_user, db)
    return StandardResponse.ok(
        data=PaymentOut.model_validate(payment).model_dump(),
        message=f"Payment {payment.voucher_number} confirmed",
    )


@router.get("/operations/{operation_id}/payments", response_model=StandardResponse)
async def list_payments(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all payments for an operation. BM and Finance Manager only."""
    payments = await PaymentService.list_payments(operation_id, db)
    items = [PaymentOut.model_validate(p).model_dump() for p in payments]
    return StandardResponse.ok(data=items, message="Payments retrieved")


# ── Standalone PFI endpoints (PFI-first flow) ─────────────────────────────────

@router.post(
    "/pfis",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_standalone_pfi(
    body: StandalonePfiCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Create a PFI before an operation exists. BM or FM."""
    pfi = await PfiService.create_standalone_pfi(body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_pfi(pfi),
        message=f"PFI {pfi.pfi_number} created",
    )


@router.get("/pfis", response_model=StandardResponse)
async def list_all_pfis(
    status: Optional[str] = Query(None),
    unlinked_only: bool = Query(False),
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """List all PFIs (global). BM and FM."""
    pfis = await PfiService.list_all_pfis(db, status_filter=status, unlinked_only=unlinked_only)
    items = [await _serialize_pfi(p) for p in pfis]
    return StandardResponse.ok(data=items, message="PFIs retrieved")


@router.post("/pfis/{pfi_id}/confirm-payment", response_model=StandardResponse)
async def confirm_pfi_payment(
    pfi_id: UUID,
    body: PfiConfirmPaymentRequest,
    current_user: User = Depends(require_roles(UserRole.finance_manager)),
    db: AsyncSession = Depends(get_db),
):
    """FM confirms PFI payment — advances PFI to 'paid', notifies BM. Finance Manager only."""
    pfi = await PfiService.confirm_pfi_payment(pfi_id, body, current_user, db)
    return StandardResponse.ok(
        data=await _serialize_pfi(pfi),
        message=f"PFI {pfi.pfi_number} payment confirmed",
    )
