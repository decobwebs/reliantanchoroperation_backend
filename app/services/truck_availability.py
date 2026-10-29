"""Where each truck is right now, and whether a plate is already registered.

AVAILABILITY IS WORKED OUT, NOT STORED. `trucks.status` was meant to track
this, but nothing ever wrote to it after a truck was registered — on
29 Oct 2026 all 432 trucks read "available", including ones out on a job.
Keeping it in step would mean touching every path that moves a truck (add,
depart, arrive, discharge, remove, operation completed, cancelled, archived)
and one missed path would leave a truck stuck as busy forever. So it is read
from the truck's live jobs instead, the same way licence balances are
computed on read rather than stored.

`trucks.status` still carries the two states a person sets by hand —
Maintenance and Out of Service — and those win over anything a job says.

A truck is on a job while its row on that job is neither completed nor
removed, and the operation itself has not finished. It becomes available
again the moment its own discharge is completed, without waiting for the
rest of the operation.
"""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OperationStatus, TruckOpStatus, TruckStatus
from app.models.operation import Operation
from app.models.truck import Truck, TruckOperation

# Set by hand on the truck; a job never overrides these.
MANUAL_STATUSES = (TruckStatus.maintenance, TruckStatus.out_of_service)

# An operation in any of these no longer holds its trucks. The four finance
# statuses are legacy end-of-job states from before finance was decoupled.
FINISHED_OPERATION_STATUSES = (
    OperationStatus.completed,
    OperationStatus.archived,
    OperationStatus.cancelled,
    OperationStatus.pfi_linked,
    OperationStatus.payment_processing,
    OperationStatus.payment_confirmed,
    OperationStatus.invoiced,
)

# A truck's row on a job, read as the truck's own status. `loading` is set
# when the truck LEAVES the loading point, so it is on the road; `arrived`
# means arrived at discharge.
_JOB_TO_TRUCK = {
    TruckOpStatus.pending: TruckStatus.assigned,
    TruckOpStatus.in_transit: TruckStatus.in_transit,
    TruckOpStatus.loading: TruckStatus.in_transit,
    TruckOpStatus.arrived: TruckStatus.discharging,
    TruckOpStatus.discharging: TruckStatus.discharging,
}

# When a truck is somehow live on two jobs, show the one furthest along.
_RANK = {TruckStatus.assigned: 0, TruckStatus.in_transit: 1, TruckStatus.discharging: 2}


@dataclass
class LiveAssignment:
    status: TruckStatus
    operation_id: UUID
    operation_number: str


async def live_assignments(
    db: AsyncSession, truck_ids: Optional[Iterable[UUID]] = None
) -> Dict[UUID, LiveAssignment]:
    """The job each truck is on right now, keyed by truck id. One query for
    the whole fleet — never call this per truck in a loop."""
    stmt = (
        select(TruckOperation.truck_id, TruckOperation.status, Operation.id, Operation.operation_number)
        .join(Operation, Operation.id == TruckOperation.operation_id)
        .where(
            and_(
                TruckOperation.status.in_(tuple(_JOB_TO_TRUCK)),
                Operation.deleted_at.is_(None),
                Operation.status.notin_(FINISHED_OPERATION_STATUSES),
            )
        )
    )
    if truck_ids is not None:
        stmt = stmt.where(TruckOperation.truck_id.in_(list(truck_ids)))

    out: Dict[UUID, LiveAssignment] = {}
    for truck_id, job_status, op_id, op_number in (await db.execute(stmt)).all():
        here = LiveAssignment(_JOB_TO_TRUCK[job_status], op_id, op_number)
        seen = out.get(truck_id)
        if seen is None or _RANK[here.status] > _RANK[seen.status]:
            out[truck_id] = here
    return out


def availability(truck: Truck, assignment: Optional[LiveAssignment]) -> TruckStatus:
    """What the truck is doing now: a hand-set state first, then its job,
    otherwise available."""
    if truck.status in MANUAL_STATUSES:
        return truck.status
    if assignment is not None:
        return assignment.status
    return TruckStatus.available


# ── Plates ────────────────────────────────────────────────────────────────────
# Ten plates were registered twice because the second entry differed only in
# spacing, a trailing full stop, or an invisible character pasted in from a
# message ("DMS 117 XA" / "DMS117XA", "NCH897XA" / "NCH897XA."). Each such
# truck had its history split across two records.

_INVISIBLE = re.compile(r"[​-‏⁠﻿]")


def clean_plate(raw: str) -> str:
    """The plate as typed, minus invisible characters, surrounding spaces and
    stray trailing punctuation. Spacing inside is kept — it is how people
    write plates — and matching ignores it anyway."""
    return _INVISIBLE.sub("", raw or "").strip().rstrip(".,;:-_").strip()


def plate_key(raw: str) -> str:
    """What two plates must share to be the same truck: letters and digits
    only, upper case."""
    return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


async def find_by_plate(
    db: AsyncSession, plate: str, exclude_id: Optional[UUID] = None
) -> Optional[Truck]:
    """An existing truck whose plate matches, ignoring spacing, case and
    punctuation."""
    key = plate_key(plate)
    if not key:
        return None
    stmt = select(Truck).where(
        func.upper(func.regexp_replace(Truck.truck_number, "[^A-Za-z0-9]", "", "g")) == key
    )
    if exclude_id is not None:
        stmt = stmt.where(Truck.id != exclude_id)
    # A merged duplicate stays on file, retired; name the live record first.
    stmt = stmt.order_by(Truck.is_active.desc(), Truck.created_at)
    return (await db.execute(stmt.limit(1))).scalar_one_or_none()
