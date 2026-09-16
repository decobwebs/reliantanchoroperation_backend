"""Delivery performance for the client portal (Phase 5).

Clients are not graded — they are shown proof of service. Quantity ordered
against quantity delivered, the variance, and how long the paperwork took.

This is a deliberate commercial move rather than a technical one. Variance is
the number every bunkering customer suspects and few suppliers will show. A
supplier that publishes it unprompted is making a trust claim competitors
would find hard to match — and the figures are already in the system, so it
costs nothing but the nerve to display them.

Scoped hard: a client sees their own operations and nothing else, using the
same `client_id` rule the portal already applies everywhere.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.kpi.service import _f
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])


class DeliveryRow(BaseModel):
    operation_id: str
    operation_number: str
    status: Optional[str] = None
    completed_at: Optional[str] = None
    receiving_vessel: Optional[str] = None
    product_type: Optional[str] = None
    quantity_delivered: Optional[float] = None
    variance_pct: Optional[float] = None
    bdn_number: Optional[str] = None
    bdn_approved: bool = False


class DeliveryPerformance(BaseModel):
    operations_total: int = 0
    operations_completed: int = 0
    deliveries_documented: int = 0
    average_variance_pct: Optional[float] = None
    # Only ever populated when the underlying figures are trustworthy.
    unit_label: str = "as recorded"
    rows: List[DeliveryRow] = Field(default_factory=list)


_Q = text("""
    SELECT o.id, o.operation_number, o.status::text AS status, o.completed_at,
           b.bdn_number, b.receiving_vessel, b.product_type,
           COALESCE(b.received_mt_vacuum, b.discharge_mt_vacuum) AS delivered,
           CASE WHEN b.discharge_gov > 0 AND b.received_gov IS NOT NULL
                THEN abs(b.discharge_gov - b.received_gov) / b.discharge_gov * 100.0
           END AS variance_pct,
           (b.status::text = 'approved') AS bdn_approved
    FROM operations o
    LEFT JOIN bdns b ON b.operation_id = o.id
    WHERE o.client_id = :cid AND o.deleted_at IS NULL
    ORDER BY o.created_at DESC
    LIMIT 60
""")


@router.get("/kpi/portal/delivery-performance", response_model=StandardResponse)
async def delivery_performance(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What we delivered to you, and how closely it matched.

    Clients only. Staff have richer views elsewhere, and letting a staff
    account through here would just be a worse version of those.
    """
    if current_user.role != UserRole.client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This view is for client accounts",
        )

    rows = (await db.execute(_Q, {"cid": str(current_user.id)})).mappings().all()

    out: List[DeliveryRow] = []
    seen_ops = set()
    # An operation with three BDNs returns three rows, so completion is
    # counted over distinct operation ids rather than rows — otherwise a
    # well-documented job inflates the client's completed count.
    completed_ops = set()
    variances: List[float] = []

    for r in rows:
        seen_ops.add(r["id"])
        if r["status"] == "completed":
            completed_ops.add(r["id"])
        v = _f(r["variance_pct"])
        if v is not None:
            variances.append(v)
        if r["bdn_number"] is None:
            continue
        out.append(DeliveryRow(
            operation_id=str(r["id"]),
            operation_number=r["operation_number"],
            status=r["status"],
            completed_at=r["completed_at"].isoformat() if r["completed_at"] else None,
            receiving_vessel=r["receiving_vessel"],
            product_type=r["product_type"],
            quantity_delivered=_f(r["delivered"]),
            variance_pct=v,
            bdn_number=r["bdn_number"],
            bdn_approved=bool(r["bdn_approved"]),
        ))

    return StandardResponse.ok(data=DeliveryPerformance(
        operations_total=len(seen_ops),
        operations_completed=len(completed_ops),
        deliveries_documented=len(out),
        average_variance_pct=(sum(variances) / len(variances)) if variances else None,
        rows=out,
    ).model_dump(mode="json"))
