"""Planned arrival times for trucks in flight (Phase 6).

Why this exists as its own screen rather than a field on the truck form: the
truck form lives inside the ~10,000-line operation detail page, and this build
is additive by rule — it does not edit code that was already running in
production. But a planned arrival that nobody can enter is worse than no
metric at all, so the capture point had to go somewhere.

A dedicated screen turns out to be the better home anyway. Setting ETAs is a
dispatcher's sweep across every truck currently moving, not something done one
operation at a time — which is exactly the view this serves.

Writes are audit-logged like every other meaningful change in RAOMS, and
changing an already-set plan requires a reason, because a plan quietly moved
after the fact would make the on-time figure meaningless.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.audit import AuditLog
from app.models.enums import UserRole
from app.models.user import User
from app.permissions import is_operation_manager
from app.schemas.common import StandardResponse

logger = logging.getLogger("raoms")

router = APIRouter(tags=["KPI"])

# Who may set a plan: the people who run the truck side, plus operation
# managers. Deliberately not everyone — a plan is the yardstick the on-time
# score is measured against.
_MAY_PLAN = (UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.logistics_officer)


class InFlightTruck(BaseModel):
    truck_op_id: UUID
    truck_id: UUID
    truck_number: str
    operation_id: UUID
    operation_number: str
    status: Optional[str] = None
    driver_name: Optional[str] = None
    vendor_name: Optional[str] = None
    discharge_location: Optional[str] = None
    departed_loading_at: Optional[datetime] = None
    expected_arrival_at: Optional[datetime] = None
    arrived_discharge_at: Optional[datetime] = None
    # Computed for the screen so every client agrees on what "late" means.
    is_late: bool = False
    hours_late: Optional[float] = None


class PlannedArrivalsOut(BaseModel):
    trucks: List[InFlightTruck] = Field(default_factory=list)
    without_plan: int = 0
    late: int = 0


class SetArrivalIn(BaseModel):
    expected_arrival_at: datetime
    # Required only when overwriting a plan that already exists.
    reason: Optional[str] = None


_Q_IN_FLIGHT = text("""
    SELECT tr.id AS truck_op_id, tr.truck_id, t.truck_number,
           o.id AS operation_id, o.operation_number,
           tr.status::text AS status,
           tr.driver_name, tr.vendor_name, tr.discharge_location,
           tr.departed_loading_at, tr.expected_arrival_at, tr.arrived_discharge_at
    FROM truck_operations tr
    JOIN trucks t     ON t.id = tr.truck_id
    JOIN operations o ON o.id = tr.operation_id
    WHERE o.deleted_at IS NULL
      AND tr.status::text NOT IN ('completed', 'cancelled')
      AND o.status::text NOT IN ('completed', 'cancelled', 'archived')
    ORDER BY
      -- Trucks already overdue first, then those with no plan, then by
      -- whichever plan falls due soonest.
      (tr.expected_arrival_at IS NOT NULL
       AND tr.arrived_discharge_at IS NULL
       AND tr.expected_arrival_at < now()) DESC,
      (tr.expected_arrival_at IS NULL) DESC,
      tr.expected_arrival_at ASC NULLS LAST
    LIMIT 120
""")


@router.get("/kpi/planned-arrivals", response_model=StandardResponse)
async def planned_arrivals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every truck still in flight, with its plan and whether it has slipped."""
    if current_user.role not in _MAY_PLAN and not is_operation_manager(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the truck and operations teams can see planned arrivals",
        )

    rows = (await db.execute(_Q_IN_FLIGHT)).mappings().all()
    now = datetime.now(timezone.utc)

    trucks: List[InFlightTruck] = []
    without_plan = late = 0
    for r in rows:
        planned = r["expected_arrival_at"]
        actual = r["arrived_discharge_at"]
        is_late = False
        hours_late = None
        if planned is not None:
            # Compare against the arrival if it happened, otherwise the clock.
            reference = actual or now
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            plan = planned if planned.tzinfo else planned.replace(tzinfo=timezone.utc)
            if reference > plan:
                is_late = True
                hours_late = (reference - plan).total_seconds() / 3600
        else:
            without_plan += 1
        if is_late:
            late += 1

        trucks.append(InFlightTruck(
            truck_op_id=r["truck_op_id"], truck_id=r["truck_id"],
            truck_number=r["truck_number"], operation_id=r["operation_id"],
            operation_number=r["operation_number"], status=r["status"],
            driver_name=r["driver_name"], vendor_name=r["vendor_name"],
            discharge_location=r["discharge_location"],
            departed_loading_at=r["departed_loading_at"],
            expected_arrival_at=planned, arrived_discharge_at=actual,
            is_late=is_late, hours_late=hours_late,
        ))

    return StandardResponse.ok(data=PlannedArrivalsOut(
        trucks=trucks, without_plan=without_plan, late=late,
    ).model_dump(mode="json"))


@router.put("/kpi/planned-arrivals/{truck_op_id}", response_model=StandardResponse)
async def set_planned_arrival(
    truck_op_id: UUID,
    body: SetArrivalIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set or move a truck's planned arrival.

    Moving an existing plan needs a written reason. Without that rule the
    on-time score would be trivially gameable — a plan nudged forward after a
    truck runs late turns a miss into a hit, and nobody would be able to tell.
    """
    if current_user.role not in _MAY_PLAN and not is_operation_manager(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the truck and operations teams can set planned arrivals",
        )

    row = (await db.execute(text("""
        SELECT tr.id, tr.expected_arrival_at, tr.operation_id, t.truck_number
        FROM truck_operations tr
        JOIN trucks t ON t.id = tr.truck_id
        WHERE tr.id = :id
    """), {"id": str(truck_op_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck assignment not found")

    previous = row["expected_arrival_at"]
    if previous is not None and not (body.reason or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Changing an existing planned arrival requires a reason",
        )
    if previous is not None and len((body.reason or "").strip()) < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reason must be at least 10 characters",
        )

    await db.execute(text("""
        UPDATE truck_operations
        SET expected_arrival_at = :at, updated_at = now()
        WHERE id = :id
    """), {"at": body.expected_arrival_at, "id": str(truck_op_id)})

    db.add(AuditLog(
        user_id=current_user.id,
        operation_id=row["operation_id"],
        entity_type="truck_operation",
        entity_id=truck_op_id,
        action="SET_EXPECTED_ARRIVAL",
        reason=(body.reason or "").strip() or None,
        changes={
            "truck_number": row["truck_number"],
            "expected_arrival_at": {
                "from": previous.isoformat() if previous else None,
                "to": body.expected_arrival_at.isoformat(),
            },
        },
    ))
    await db.commit()

    return StandardResponse.ok(data={
        "truck_op_id": str(truck_op_id),
        "expected_arrival_at": body.expected_arrival_at.isoformat(),
        "was_set_before": previous is not None,
    })
