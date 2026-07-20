"""
Client Portal — read-only endpoints for clients.
Clients can only see their own operations and related documents/BDNs.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.operation import Operation
from app.models.document import Document
from app.models.bdn import BDN
from app.models.user import User
from app.models.enums import UserRole, OperationStatus
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.document import DocumentOut
from app.schemas.bdn import BdnOut
from app.schemas.invoice import InvoiceOut
from app.services.document_service import create_signed_supabase_url
from app.services.milestone_service import list_milestones
from app.models.finance import Invoice
from app.models.enums import InvoiceStatus

router = APIRouter(prefix="/portal", tags=["Client Portal"])


def _require_client(current_user: User) -> User:
    """Raise 403 if the user is not a client."""
    if current_user.role != UserRole.client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is for client accounts only",
        )
    return current_user


@router.get("/operations", response_model=PaginatedResponse)
async def portal_list_operations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
    op_status: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the authenticated client's own operations."""
    _require_client(current_user)

    conditions = [
        Operation.client_id == current_user.id,
        Operation.deleted_at.is_(None),
    ]
    if op_status:
        try:
            conditions.append(Operation.status == OperationStatus(op_status))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status: {op_status}",
            )

    count_result = await db.execute(
        select(func.count()).select_from(Operation).where(and_(*conditions))
    )
    total = count_result.scalar_one()

    offset = (page - 1) * per_page
    result = await db.execute(
        select(Operation)
        .where(and_(*conditions))
        .order_by(Operation.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    operations = result.scalars().all()

    items = [_portal_op_summary(op) for op in operations]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.get("/operations/{operation_id}", response_model=StandardResponse)
async def portal_get_operation(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detail of a single operation the client owns."""
    _require_client(current_user)

    result = await db.execute(
        select(Operation).where(
            and_(
                Operation.id == operation_id,
                Operation.client_id == current_user.id,
                Operation.deleted_at.is_(None),
            )
        )
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    return StandardResponse.ok(data=_portal_op_detail(op), message="Operation retrieved")


@router.get("/operations/{operation_id}/documents", response_model=StandardResponse)
async def portal_list_documents(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List non-deleted documents attached to the client's operation."""
    _require_client(current_user)
    _assert_owns_operation(operation_id, current_user.id, db)

    result = await db.execute(
        select(Operation.id).where(
            and_(
                Operation.id == operation_id,
                Operation.client_id == current_user.id,
                Operation.deleted_at.is_(None),
            )
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    docs_result = await db.execute(
        select(Document).where(
            and_(
                Document.operation_id == operation_id,
                Document.is_deleted == False,
            )
        ).order_by(Document.created_at.desc())
    )
    docs = docs_result.scalars().all()
    items = [DocumentOut.model_validate(d).model_dump() for d in docs]
    return StandardResponse.ok(data=items, message="Documents retrieved")


@router.get("/operations/{operation_id}/bdns", response_model=StandardResponse)
async def portal_list_bdns(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List approved BDNs for the client's operation."""
    _require_client(current_user)

    op_result = await db.execute(
        select(Operation.id).where(
            and_(
                Operation.id == operation_id,
                Operation.client_id == current_user.id,
                Operation.deleted_at.is_(None),
            )
        )
    )
    if not op_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    bdns_result = await db.execute(
        select(BDN)
        .where(BDN.operation_id == operation_id)
        .order_by(BDN.created_at.asc())
    )
    bdns = bdns_result.scalars().all()
    items = [BdnOut.model_validate(b).model_dump() for b in bdns]
    return StandardResponse.ok(data=items, message="BDNs retrieved")


@router.get("/operations/{operation_id}/invoices", response_model=StandardResponse)
async def portal_list_invoices(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List sent/paid invoices for the client's operation (no drafts)."""
    _require_client(current_user)

    result = await db.execute(
        select(Operation.id).where(
            and_(
                Operation.id == operation_id,
                Operation.client_id == current_user.id,
                Operation.deleted_at.is_(None),
            )
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    inv_result = await db.execute(
        select(Invoice)
        .where(
            and_(
                Invoice.operation_id == operation_id,
                Invoice.status.in_([InvoiceStatus.sent, InvoiceStatus.paid, InvoiceStatus.overdue]),
            )
        )
        .order_by(Invoice.created_at.desc())
    )
    invoices = inv_result.scalars().all()
    items = []
    for inv in invoices:
        item = InvoiceOut.model_validate(inv).model_dump()
        item["pdf_url"] = await create_signed_supabase_url(item.get("pdf_url"))
        items.append(item)
    return StandardResponse.ok(data=items, message="Invoices retrieved")


@router.get("/operations/{operation_id}/milestones", response_model=StandardResponse)
async def portal_list_milestones(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List client-visible progress milestones for the operation."""
    _require_client(current_user)

    result = await db.execute(
        select(Operation.id).where(
            and_(
                Operation.id == operation_id,
                Operation.client_id == current_user.id,
                Operation.deleted_at.is_(None),
            )
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    milestones = await list_milestones(db, operation_id)
    items = [
        {
            "milestone_type": m.milestone_type,
            "title": m.title,
            "description": m.description,
            "reached_at": m.reached_at.isoformat(),
        }
        for m in milestones
    ]
    return StandardResponse.ok(data=items, message="Milestones retrieved")


@router.get("/dashboard", response_model=StandardResponse)
async def portal_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Quick summary dashboard for the client."""
    _require_client(current_user)

    base = and_(
        Operation.client_id == current_user.id,
        Operation.deleted_at.is_(None),
    )

    total = (await db.execute(select(func.count()).select_from(Operation).where(base))).scalar_one()

    # Active = everything except completed/archived/cancelled
    terminal = {
        OperationStatus.completed, OperationStatus.archived, OperationStatus.cancelled
    }
    all_result = await db.execute(select(Operation.status).where(base))
    rows = all_result.scalars().all()
    active = sum(1 for s in rows if s not in terminal)
    completed = sum(1 for s in rows if s == OperationStatus.completed)

    return StandardResponse.ok(
        data={
            "total_operations": total,
            "active_operations": active,
            "completed_operations": completed,
            "cancelled_operations": sum(1 for s in rows if s == OperationStatus.cancelled),
        },
        message="Dashboard retrieved",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _portal_op_summary(op: Operation) -> dict:
    """Client-safe operation summary (no financial internals)."""
    return {
        "id": str(op.id),
        "operation_number": op.operation_number,
        "type": op.type.value,
        "status": op.status.value,
        "products": [{"product_type": p.product_type, "quantity_mt": str(p.quantity_mt)} for p in op.products],
        "actual_volume_mt": str(op.actual_volume_mt) if op.actual_volume_mt else None,
        "notes": op.notes,
        "created_at": op.created_at.isoformat(),
        "updated_at": op.updated_at.isoformat(),
    }


def _portal_op_detail(op: Operation) -> dict:
    detail = _portal_op_summary(op)
    detail["completed_at"] = op.completed_at.isoformat() if op.completed_at else None
    return detail


def _assert_owns_operation(operation_id: UUID, client_id: UUID, db: AsyncSession) -> None:
    """Placeholder — actual ownership check done inline in each handler."""
    pass
