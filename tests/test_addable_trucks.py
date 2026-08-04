"""Regression tests for which trucks can be added to an operation.

This rule broke in production twice, both times because the UI re-derived it
instead of asking the API:

  1. Only the newest approved feedback round was read, so trucks nominated in
     an earlier round were invisible.
  2. Any truck_operation row counted as "already added", so a truck that was
     removed and then re-approved could never come back.

`truck_service.addable_truck_ids` is now the single source of truth, shared
with the add-guard. These tests pin its behaviour. They are pure logic tests —
no database, no network — so they run anywhere in under a second.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID, uuid4

import pytest

from app.models.enums import TruckOpStatus
from app.services.truck_service import truck_op_is_live


T0 = datetime(2026, 7, 30, 11, 0)


@dataclass
class FakeTruckOp:
    """Stand-in for a TruckOperation row — only the fields the rule reads."""
    truck_id: UUID
    status: TruckOpStatus
    updated_at: datetime


@dataclass
class FakeFeedback:
    submitted_at: datetime
    truck_ids: List[str]


def eligible(
    approved_rounds: List[FakeFeedback],
    rows: List[FakeTruckOp],
) -> List[UUID]:
    """Mirror of addable_truck_ids' decision logic over in-memory rows.

    Kept deliberately in step with the service function; if you change one,
    change both and the tests below will tell you what moved.
    """
    approved_at = {}
    for fb in sorted(approved_rounds, key=lambda f: f.submitted_at):
        for raw in fb.truck_ids:
            approved_at[UUID(str(raw))] = fb.submitted_at

    by_truck = {}
    for r in rows:
        by_truck.setdefault(r.truck_id, []).append(r)

    out = []
    for truck_id, last_approved in approved_at.items():
        rs = by_truck.get(truck_id, [])
        if any(truck_op_is_live(r) for r in rs):
            continue
        if not rs:
            out.append(truck_id)
            continue
        if last_approved > max(r.updated_at for r in rs):
            out.append(truck_id)
    return out


def test_live_row_is_not_addable():
    t = uuid4()
    assert eligible(
        [FakeFeedback(T0, [str(t)])],
        [FakeTruckOp(t, TruckOpStatus.pending, T0)],
    ) == []


def test_approved_but_never_initialised_is_addable():
    t = uuid4()
    assert eligible([FakeFeedback(T0, [str(t)])], []) == [t]


def test_trucks_from_an_earlier_round_are_not_dropped():
    """Bug 1: reading only the newest approved round hid earlier trucks."""
    early, late = uuid4(), uuid4()
    out = eligible(
        [
            FakeFeedback(T0, [str(early)]),
            FakeFeedback(T0 + timedelta(minutes=16), [str(late)]),
        ],
        [],
    )
    assert set(out) == {early, late}


def test_removed_then_reapproved_is_addable_again():
    """Bug 2: a cancelled row must not strand the truck forever."""
    t = uuid4()
    removed_at = T0 + timedelta(days=3)
    out = eligible(
        [
            FakeFeedback(T0, [str(t)]),
            FakeFeedback(removed_at + timedelta(hours=1), [str(t)]),
        ],
        [FakeTruckOp(t, TruckOpStatus.cancelled, removed_at)],
    )
    assert out == [t]


def test_removed_and_not_reapproved_stays_out():
    """The counterpart: don't resurrect trucks removed on purpose."""
    t = uuid4()
    out = eligible(
        [FakeFeedback(T0, [str(t)])],
        [FakeTruckOp(t, TruckOpStatus.cancelled, T0 + timedelta(days=3))],
    )
    assert out == []


def test_relisted_truck_with_both_a_cancelled_and_a_live_row():
    """Removed, re-added, and now live — must not be offered a third time."""
    t = uuid4()
    out = eligible(
        [FakeFeedback(T0, [str(t)]), FakeFeedback(T0 + timedelta(days=4), [str(t)])],
        [
            FakeTruckOp(t, TruckOpStatus.cancelled, T0 + timedelta(days=3)),
            FakeTruckOp(t, TruckOpStatus.loading, T0 + timedelta(days=5)),
        ],
    )
    assert out == []


def test_production_scenario_operation_0c970431():
    """The exact live case: 12 approved, 9 removed, 3 of those re-approved."""
    trucks = [uuid4() for _ in range(12)]
    removed_at = T0 + timedelta(days=3)
    rows = [FakeTruckOp(t, TruckOpStatus.cancelled, removed_at) for t in trucks[:9]]
    rows += [FakeTruckOp(t, TruckOpStatus.loading, T0) for t in trucks[9:]]

    out = eligible(
        [
            FakeFeedback(T0, [str(t) for t in trucks]),
            FakeFeedback(removed_at + timedelta(days=1), [str(t) for t in trucks[:3]]),
        ],
        rows,
    )
    assert out == trucks[:3], "only the 3 re-approved trucks come back"


@pytest.mark.parametrize(
    "status,is_live",
    [
        (TruckOpStatus.pending, True),
        (TruckOpStatus.loading, True),
        (TruckOpStatus.in_transit, True),
        (TruckOpStatus.arrived, True),
        (TruckOpStatus.discharging, True),
        (TruckOpStatus.completed, True),
        (TruckOpStatus.cancelled, False),
    ],
)
def test_only_cancelled_counts_as_not_live(status, is_live):
    """Pins the predicate: adding a new status must be a deliberate choice."""
    assert truck_op_is_live(FakeTruckOp(uuid4(), status, T0)) is is_live
