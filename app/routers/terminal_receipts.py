from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.terminal_receipt import TerminalLoadingReceiptCreate, TerminalLoadingReceiptOut, QuantitySummaryOut
from app.services.terminal_receipt_service import TerminalReceiptService

router = APIRouter(tags=["Terminal Loading Receipts"])

_record_roles = Depends(require_roles(UserRole.bunker_manager, UserRole.cargo_superintendent, UserRole.ops_supervisor, UserRole.marine_operator))
_view_roles = Depends(require_roles(
    UserRole.bunker_manager, UserRole.cargo_superintendent, UserRole.ops_supervisor,
    UserRole.marine_operator, UserRole.finance_manager,
))


@router.post(
    "/operations/{operation_id}/terminal-receipts",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_receipt(
    operation_id: UUID,
    body: TerminalLoadingReceiptCreate,
    current_user: User = _record_roles,
    db: AsyncSession = Depends(get_db),
):
    receipt = await TerminalReceiptService.create_receipt(operation_id, body, current_user, db)
    data = TerminalLoadingReceiptOut.model_validate(receipt).model_dump()
    data["recorded_by_name"] = getattr(receipt, "_recorded_by_name", None)
    return StandardResponse.ok(data=data, message="Terminal loading receipt recorded")


@router.get("/operations/{operation_id}/terminal-receipts", response_model=StandardResponse)
async def list_terminal_receipts(
    operation_id: UUID,
    current_user: User = _view_roles,
    db: AsyncSession = Depends(get_db),
):
    receipts = await TerminalReceiptService.list_receipts(operation_id, db)
    items = []
    for r in receipts:
        item = TerminalLoadingReceiptOut.model_validate(r).model_dump()
        item["recorded_by_name"] = getattr(r, "_recorded_by_name", None)
        items.append(item)
    return StandardResponse.ok(data=items)


@router.get("/operations/{operation_id}/quantity-summary", response_model=StandardResponse)
async def get_quantity_summary(
    operation_id: UUID,
    current_user: User = _view_roles,
    db: AsyncSession = Depends(get_db),
):
    summary = await TerminalReceiptService.quantity_summary(operation_id, db)
    return StandardResponse.ok(data=summary.model_dump())
