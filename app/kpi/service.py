"""Computes a KPI scorecard for one operation.

Everything here is derived from records the system already writes. The KPI
module asks no user to enter anything new, which is the whole reason it can
be switched on without changing anyone's daily work.

Two conventions carried over from the rest of the codebase:

* **Computed on read**, like licence balances. Nothing is stored, so a
  corrected figure is reflected the moment it is corrected. Monthly rollups
  (a later phase) are the exception and get frozen, because a month that has
  been graded should not silently restate itself.
* **Absent is not zero.** A metric with nothing to measure returns None and
  is dropped from the weighted mean, never scored 0. A truck-only operation
  is not a failing vessel operation.

LATENCY NOTE. The API runs in Oregon and the database is in Frankfurt, so a
round trip is expensive, and the connection pool is deliberately small (see
the connection-budget comment in app/database.py — exceeding it has caused a
production outage before). That rules out fanning these queries out across
parallel sessions. Instead this gathers everything in one pass of focused
queries on the request's own session, and no query runs inside a loop. If the
scorecard ever needs to be faster, the fix is to fold the aggregates into a
single multi-CTE statement, not to open more connections.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.catalog import (
    MetricDef, metrics_for_role, rating_for, score_value, weighted_score,
)
from app.kpi.models import KpiTarget
from app.kpi.schemas import (
    ContributorOut, MetricOut, OperationScorecardOut, RoleScoreOut,
)
from app.models.bdn import BDN, VesselActivity, VesselActivityLeg
from app.models.enums import (
    AuditResult, BdnStatus, FeedbackStatus, OperationStatus, OperationType,
    UserRole, VesselActivityStatus, role_label,
)
from app.models.finance import Invoice, PfiAllocation, Voucher
from app.models.operation import Operation, OperationStatusHistory, TaskAssignment, TruckFeedback
from app.models.truck import TruckBdn, TruckOperation, TruckSafetyAudit
from app.models.user import User


# ── small helpers ─────────────────────────────────────────────────────────

def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize to UTC-aware. Several timestamp columns are caller-supplied
    with no timezone requirement, and subtracting a naive from an aware
    datetime raises — this keeps that from ever reaching a user."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _hours(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[float]:
    a, b = _aware(earlier), _aware(later)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds() / 3600
    # A negative duration means the two timestamps were logged out of order.
    # That is a data problem, not a performance of -3 hours, so it is dropped
    # rather than flattered into a perfect score.
    return delta if delta >= 0 else None


def _f(value) -> Optional[float]:
    """Decimal/None -> float/None, without turning None into 0.0."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _pct_variance(sent: Optional[float], received: Optional[float]) -> Optional[float]:
    """Variance as a percentage of what was sent. Absolute value: losing 2%
    and gaining 2% are both a measurement that failed to reconcile, and the
    documents treat any variance as the thing to drive to zero."""
    if sent is None or received is None or sent == 0:
        return None
    return abs(sent - received) / abs(sent) * 100.0


class _Value:
    """A computed metric value, or a reason there isn't one — never both.

    The distinction matters more than it looks. "Measured, and the answer is
    zero" and "nothing to measure" are different facts about a person's work,
    and conflating them is how a KPI tool starts lying: an operation that has
    not reached its finance stage yet would score Finance zero and read
    "Unsatisfactory", which is untrue and would rightly destroy trust in the
    whole system. Supplying a reason therefore forces the value to None, so
    the contradiction cannot be expressed.
    """

    __slots__ = ("value", "sample_size", "no_data_reason")

    def __init__(self, value: Optional[float] = None, sample_size: Optional[int] = None,
                 no_data_reason: Optional[str] = None):
        self.value = None if no_data_reason else value
        self.sample_size = sample_size
        self.no_data_reason = no_data_reason


_NOT_APPLICABLE = "Not applicable to this operation type"
_NOT_RECORDED = "Not recorded yet"


# ── target resolution ─────────────────────────────────────────────────────

class _Override:
    __slots__ = ("target", "fail", "weight", "at", "by", "reason")

    def __init__(self, target, fail, weight, at, by, reason):
        self.target, self.fail, self.weight = target, fail, weight
        self.at, self.by, self.reason = at, by, reason


async def resolve_overrides(db: AsyncSession) -> Dict[str, _Override]:
    """The newest in-effect kpi_targets row per metric.

    The table is append-only, so "current" means the latest row whose
    effective_from has already passed. Rows dated in the future are ignored
    until they arrive, which is what makes a target change announceable in
    advance.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(KpiTarget, User.full_name)
        .join(User, User.id == KpiTarget.set_by, isouter=True)
        .where(KpiTarget.effective_from <= now, KpiTarget.is_active.is_(True))
        .order_by(KpiTarget.metric_key, KpiTarget.effective_from.desc(), KpiTarget.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    out: Dict[str, _Override] = {}
    for target_row, setter_name in rows:
        # Ordered newest-first per key, so the first one wins and later
        # (older) rows for the same key are skipped.
        if target_row.metric_key in out:
            continue
        out[target_row.metric_key] = _Override(
            target=_f(target_row.target_value),
            fail=_f(target_row.fail_value),
            weight=_f(target_row.weight),
            at=target_row.effective_from,
            by=setter_name,
            reason=target_row.reason,
        )
    return out


# ── gathering ─────────────────────────────────────────────────────────────

class _Data:
    """Everything one scorecard needs, fetched up front."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


async def _gather(operation: Operation, assignments: Sequence[TaskAssignment],
                  db: AsyncSession) -> _Data:
    op_id = operation.id

    truck_ops = (await db.execute(
        select(TruckOperation).where(TruckOperation.operation_id == op_id)
    )).scalars().all()

    # TruckSafetyAudit carries operation_id directly, so this needs no
    # dependency on the truck rows above.
    audits = (await db.execute(
        select(TruckSafetyAudit).where(TruckSafetyAudit.operation_id == op_id)
    )).scalars().all()

    activities = (await db.execute(
        select(VesselActivity).where(
            VesselActivity.operation_id == op_id,
            VesselActivity.status != VesselActivityStatus.cancelled,
        )
    )).scalars().all()

    # Legs have no operation_id of their own; a subquery keeps this to one
    # round trip rather than a second query keyed on the activity ids.
    legs = (await db.execute(
        select(VesselActivityLeg).where(
            VesselActivityLeg.vessel_activity_id.in_(
                select(VesselActivity.id).where(VesselActivity.operation_id == op_id)
            ),
            VesselActivityLeg.cancelled_at.is_(None),
        )
    )).scalars().all()

    bdns = (await db.execute(select(BDN).where(BDN.operation_id == op_id))).scalars().all()
    truck_bdns = (await db.execute(select(TruckBdn).where(TruckBdn.operation_id == op_id))).scalars().all()

    history = (await db.execute(
        select(OperationStatusHistory)
        .where(OperationStatusHistory.operation_id == op_id)
        .order_by(OperationStatusHistory.created_at)
    )).scalars().all()

    feedback = (await db.execute(
        select(TruckFeedback).where(TruckFeedback.operation_id == op_id)
    )).scalars().all()

    vouchers = (await db.execute(select(Voucher).where(Voucher.operation_id == op_id))).scalars().all()
    invoices = (await db.execute(select(Invoice).where(Invoice.operation_id == op_id))).scalars().all()
    allocations = (await db.execute(
        select(PfiAllocation).where(PfiAllocation.operation_id == op_id)
    )).scalars().all()

    # Contributors: one query covering task assignees, vessel-run assignees
    # and whoever logged each truck.
    people_ids = (
        {ta.assigned_to for ta in assignments}
        | {a.assigned_to for a in activities if a.assigned_to}
        | {t.logged_by for t in truck_ops if t.logged_by}
    )
    people: List[User] = []
    if people_ids:
        people = (await db.execute(select(User).where(User.id.in_(people_ids)))).scalars().all()

    return _Data(
        truck_ops=truck_ops, audits=audits, activities=activities, legs=legs,
        bdns=bdns, truck_bdns=truck_bdns, history=history, feedback=feedback,
        vouchers=vouchers, invoices=invoices, allocations=allocations,
        people=people,
    )


# ── metric computation ────────────────────────────────────────────────────

def _first_pass_rate(rows) -> Tuple[Optional[float], int]:
    """Share of decided BDNs approved without having been rejected first.

    A row that is still pending has not been judged yet and is excluded, so
    submitting a BDN never moves this number until someone acts on it.
    """
    decided = [r for r in rows if r.status in (BdnStatus.approved, BdnStatus.rejected)]
    if not decided:
        return None, 0
    clean = [
        r for r in decided
        if r.status == BdnStatus.approved and not (r.rejection_reason or "").strip()
    ]
    return len(clean) / len(decided) * 100.0, len(decided)


def _truck_metrics(d: _Data, op: Operation) -> Dict[str, _Value]:
    out: Dict[str, _Value] = {}
    trucks = d.truck_ops

    if not trucks:
        reason = _NOT_APPLICABLE if op.type == OperationType.vessel_only else _NOT_RECORDED
        for key in ("truck.loss_litres_per_truck", "truck.discharge_hours",
                    "truck.mobilization_hours", "truck.loading_turnaround_hours",
                    "truck.depart_after_loading_hours", "truck.safety_audit_pct"):
            out[key] = _Value(no_data_reason=reason)
        return out

    # ── loss per truck, in litres ──
    # UNITS — READ BEFORE CHANGING. truck_operations.quantity_loaded_mt,
    # quantity_discharged_mt and variance_mt are named `_mt` but hold
    # LITRES. Verified against production: trucks load ~40,000 against a
    # ~42,000 capacity, and variance averages ~1,929, which only makes sense
    # in litres and sits exactly where the Truck Supervisor document's
    # 1,500-litre cap sits. This matches the business rule that trucks stay
    # in litres; the `_mt` suffix is a legacy name, not a unit. No density
    # conversion belongs here — applying one inflated the figure by roughly
    # a thousand times.
    losses: List[float] = []
    for t in trucks:
        variance = _f(t.variance_mt)
        if variance is None:
            loaded, discharged = _f(t.quantity_loaded_mt), _f(t.quantity_discharged_mt)
            if loaded is not None and discharged is not None:
                variance = loaded - discharged
        if variance is not None:
            losses.append(abs(variance))
    out["truck.loss_litres_per_truck"] = (
        _Value(_mean(losses), len(losses))
        if losses else
        _Value(no_data_reason="No truck has both a loaded and a discharged figure recorded")
    )

    # ── movement timings ──
    def _timing(pairs: List[Optional[float]], missing: str) -> _Value:
        present = [p for p in pairs if p is not None]
        return _Value(_mean(present), len(present)) if present else _Value(no_data_reason=missing)

    out["truck.discharge_hours"] = _timing(
        [_hours(t.discharge_start_at, t.discharge_end_at) for t in trucks],
        "No truck has both a discharge start and end time recorded",
    )
    out["truck.mobilization_hours"] = _timing(
        [_hours(t.created_at, t.arrived_loading_at) for t in trucks],
        "No truck has an arrival-at-loading time recorded",
    )
    out["truck.loading_turnaround_hours"] = _timing(
        [_hours(t.arrived_loading_at, t.departed_loading_at) for t in trucks],
        "No truck has both arrival and departure times at the loading point",
    )
    out["truck.depart_after_loading_hours"] = _timing(
        [_hours(t.departed_loading_at, t.transit_start_at) for t in trucks],
        "No truck has a transit start time recorded",
    )

    # ── on-time delivery ──
    # Unlike the safety audits below, an absent plan is NOT a failing. A truck
    # with no planned arrival was never given a time to beat, so counting it
    # as late would punish the dispatcher's omission as if it were the
    # driver's lateness. Only planned-and-arrived trucks are judged.
    planned = [
        t for t in trucks
        if getattr(t, "expected_arrival_at", None) is not None
        and t.arrived_discharge_at is not None
    ]
    if planned:
        on_time = sum(
            1 for t in planned
            if _aware(t.arrived_discharge_at) <= _aware(t.expected_arrival_at)
        )
        out["truck.on_time_delivery_pct"] = _Value(on_time / len(planned) * 100.0,
                                                   sample_size=len(planned))
    else:
        out["truck.on_time_delivery_pct"] = _Value(
            None,
            no_data_reason="No truck on this operation has both a planned arrival "
                           "and a recorded arrival",
        )

    # ── the Data Trust Score ──
    # RAOMS snapshots what it had already recorded into the system_* columns at
    # the moment a Truck BDN is submitted, precisely so the two can be compared.
    # This measures whether the paperwork can be trusted — a different question
    # from whether the operation went well, and one no other figure here asks.
    #
    # A pair is only judged when both sides exist and the system side is
    # non-zero; dividing by an absent baseline would invent a discrepancy.
    gaps: List[float] = []
    for b in d.truck_bdns:
        pairs = (
            (b.quantity_loaded_mt, b.system_quantity_loaded_mt),
            (b.quantity_discharged_mt, b.system_quantity_discharged_mt),
        )
        per_bdn = [
            abs(float(sub) - float(sys_)) / float(sys_) * 100.0
            for sub, sys_ in pairs
            if sub is not None and sys_ is not None and float(sys_) != 0
        ]
        if per_bdn:
            gaps.append(_mean(per_bdn))
    if gaps:
        out["truck.figure_gap_pct"] = _Value(_mean(gaps), sample_size=len(gaps))
    else:
        out["truck.figure_gap_pct"] = _Value(
            None,
            no_data_reason="No Truck BDN on this operation carries both a submitted "
                           "and a system-recorded quantity to compare",
        )

    # ── safety audits: both phases present AND both satisfactory ──
    # Always scored once there are trucks. No audits recorded genuinely is
    # 0% compliance — the documents require a Pre and a Post audit on every
    # truck, so this is a measured failing, not missing data.
    by_truck: Dict = {}
    for a in d.audits:
        by_truck.setdefault(a.truck_op_id, []).append(a)
    good = 0
    for t in trucks:
        got = by_truck.get(t.id, [])
        phases = {a.phase for a in got}
        all_ok = got and all(a.result == AuditResult.satisfactory for a in got)
        if len(phases) >= 2 and all_ok:
            good += 1
    out["truck.safety_audit_pct"] = _Value(good / len(trucks) * 100.0, len(trucks))

    return out


def _vessel_metrics(d: _Data, op: Operation) -> Dict[str, _Value]:
    out: Dict[str, _Value] = {}
    has_vessel_work = bool(d.activities or d.legs)

    if not has_vessel_work:
        reason = _NOT_APPLICABLE if op.type == OperationType.truck_only else _NOT_RECORDED
        for key in ("marine.cargo_loss_pct", "marine.reporting_lag_hours",
                    "marine.hse_completion_pct", "marine.documentation_pct",
                    "marine.safety_incidents"):
            out[key] = _Value(no_data_reason=reason)
        return out

    # ── cargo loss: what the barge discharged vs what the ship acknowledged ──
    # The BDN is the document of record for both sides and holds the two
    # readings as independent figures, which is what makes the comparison
    # meaningful rather than circular.
    variances = [
        v for v in (
            _pct_variance(_f(b.discharge_gov), _f(b.received_gov)) for b in d.bdns
        ) if v is not None
    ]
    if not variances:
        # Full-operation runs reconcile trucks against the barge instead.
        variances = [
            v for v in (
                _pct_variance(_f(a.truck_delivered_mt), _f(a.vessel_received_mt))
                for a in d.activities
            ) if v is not None
        ]
    out["marine.cargo_loss_pct"] = (
        _Value(_mean(variances), len(variances)) if variances
        else _Value(no_data_reason="No BDN carries both a discharged and a received reading")
    )

    # ── two-clock reporting lag ──
    # Legs store when a stage happened (user) and when it was typed in
    # (system). The gap is the "real-time updates" KPI, measured rather than
    # asserted. Only legs keep both clocks; the older six-stage flow has a
    # single timestamp, so it cannot contribute here.
    lags: List[float] = []
    for leg in d.legs:
        for user_at, system_at in (
            (leg.stage_cast_off_user_at, leg.stage_cast_off_system_at),
            (leg.stage_alongside_user_at, leg.stage_alongside_system_at),
            (leg.stage_discharge_commenced_user_at, leg.stage_discharge_commenced_system_at),
            (leg.stage_discharge_completed_user_at, leg.stage_discharge_completed_system_at),
        ):
            lag = _hours(user_at, system_at)
            if lag is not None:
                lags.append(lag)
    out["marine.reporting_lag_hours"] = (
        _Value(_mean(lags), len(lags)) if lags
        else _Value(no_data_reason="No stage recorded both when it happened and when it was entered")
    )

    # ── HSE completeness ──
    required = 0
    done = 0
    for a in d.activities:
        # A full-operation run carries three checks: pre, during and post.
        required += 3
        done += sum(1 for ts in (a.hse_conducted_at, a.hse_during_conducted_at,
                                 a.hse_post_conducted_at) if ts is not None)
    for leg in d.legs:
        # Each delivery leg carries its own single check.
        required += 1
        done += 1 if leg.hse_conducted_at is not None else 0
    out["marine.hse_completion_pct"] = (
        _Value(done / required * 100.0, required) if required
        else _Value(no_data_reason=_NOT_RECORDED)
    )

    # ── measurement chain completeness ──
    chains: List[float] = []
    for leg in d.legs:
        fields = (leg.gov, leg.vcf, leg.gsv, leg.mt_vacuum, leg.density)
        chains.append(sum(1 for f in fields if f is not None) / len(fields) * 100.0)
    for a in d.activities:
        fields = (a.loading_gov, a.loading_vcf, a.loading_gsv, a.loading_mt_vacuum, a.loading_density)
        if any(f is not None for f in fields):
            chains.append(sum(1 for f in fields if f is not None) / len(fields) * 100.0)
    out["marine.documentation_pct"] = (
        _Value(_mean(chains), len(chains)) if chains
        else _Value(no_data_reason="No quantity readings recorded yet")
    )

    # ── incidents ──
    incidents = 0
    for a in d.activities:
        if (_f(a.spillage_mt) or 0) > 0:
            incidents += 1
        incidents += sum(
            1 for r in (a.hse_result, a.hse_during_result, a.hse_post_result)
            if r == AuditResult.not_satisfactory
        )
    for leg in d.legs:
        if leg.hse_result == AuditResult.not_satisfactory:
            incidents += 1
    out["marine.safety_incidents"] = _Value(float(incidents), len(d.activities) + len(d.legs))

    return out


def _ops_metrics(d: _Data, op: Operation, truck: Dict[str, _Value],
                 marine: Dict[str, _Value]) -> Dict[str, _Value]:
    out: Dict[str, _Value] = {}

    # ── whole-operation loss ──
    # UNITS. The two sides of this operation are measured in different units
    # — trucks in litres (see the note in _truck_metrics), the vessel side in
    # MT(vac) — so the comparison is made strictly within one side and never
    # across them. An earlier version divided a litre total by an MT total
    # and reported a 71% loss on a healthy operation.
    # Only records carrying BOTH sides are counted. Summing every loaded
    # figure against only those that also have a discharged figure compares
    # a whole fleet against part of one, and reported a 71% loss on an
    # operation whose real loss was a few percent. A truck still in transit
    # must drop out of both totals, not just the second.
    def _paired(rows, left_of, right_of) -> Tuple[float, float, int]:
        left = right = 0.0
        n = 0
        for row in rows:
            a, b = left_of(row), right_of(row)
            if a is not None and b is not None:
                left += a
                right += b
                n += 1
        return left, right, n

    truck_loaded, truck_discharged, truck_n = _paired(
        d.truck_ops,
        lambda t: _f(t.quantity_loaded_mt),
        lambda t: _f(t.quantity_discharged_mt),
    )
    vessel_out, vessel_in, vessel_n = _paired(
        d.bdns,
        lambda b: _f(b.discharge_mt_vacuum),
        lambda b: _f(b.received_mt_vacuum),
    )

    if truck_loaded and truck_discharged:
        # Trucks are the loading leg on truck-only and full operations, and
        # are where loss overwhelmingly occurs.
        value, sample = _pct_variance(truck_loaded, truck_discharged), truck_n
    elif vessel_out and vessel_in:
        value, sample = _pct_variance(vessel_out, vessel_in), vessel_n
    else:
        value, sample = None, None

    out["ops.operation_loss_pct"] = (
        _Value(value, sample) if value is not None
        else _Value(no_data_reason="Loaded and delivered totals are not both recorded yet")
    )

    # The same measurement as the Marine Operations loss metric, carried at
    # the far tighter Marine & Cargo Assistant target. Deliberately not
    # recomputed — one number, two thresholds.
    vessel_variance = marine.get("marine.cargo_loss_pct")
    out["ops.vessel_variance_pct"] = vessel_variance or _Value(no_data_reason=_NOT_RECORDED)

    # ── mobilization: created to staffed ──
    left_draft = next(
        (h.created_at for h in d.history if h.to_status != OperationStatus.draft), None
    )
    out["ops.mobilization_response_hours"] = (
        _Value(_hours(op.created_at, left_draft), 1) if left_draft
        else _Value(no_data_reason="Operation has not left Draft")
    )

    # ── approval turnaround across every approvable thing ──
    waits: List[float] = []
    for b in list(d.bdns) + list(d.truck_bdns):
        if b.approved_at:
            w = _hours(b.created_at, b.approved_at)
            if w is not None:
                waits.append(w)
    for f in d.feedback:
        if f.reviewed_at and f.status != FeedbackStatus.pending:
            w = _hours(f.submitted_at, f.reviewed_at)
            if w is not None:
                waits.append(w)
    out["ops.approval_turnaround_hours"] = (
        _Value(_mean(waits), len(waits)) if waits
        else _Value(no_data_reason="Nothing has been approved on this operation yet")
    )

    # ── longest silence while active ──
    # Every timestamp that represents somebody doing something, sorted; the
    # biggest gap between consecutive events is how long the operation went
    # unattended.
    events: List[datetime] = [_aware(op.created_at)]
    events += [_aware(h.created_at) for h in d.history]
    for t in d.truck_ops:
        events += [_aware(x) for x in (t.created_at, t.arrived_loading_at, t.departed_loading_at,
                                       t.discharge_start_at, t.discharge_end_at)]
    for a in d.activities:
        events += [_aware(x) for x in (a.created_at, a.stage_cast_off_at, a.stage_alongside_at,
                                       a.stage_commence_discharge_at, a.stage_discharge_completed_at)]
    for leg in d.legs:
        events += [_aware(x) for x in (leg.created_at, leg.stage_cast_off_system_at,
                                       leg.stage_alongside_system_at,
                                       leg.stage_discharge_commenced_system_at,
                                       leg.stage_discharge_completed_system_at)]
    events += [_aware(b.created_at) for b in list(d.bdns) + list(d.truck_bdns)]
    ordered = sorted(e for e in events if e is not None)
    if len(ordered) >= 2:
        # An operation still running is measured up to now, so a live
        # operation that has gone quiet shows the silence as it grows.
        end = _aware(op.completed_at) or datetime.now(timezone.utc)
        if end > ordered[-1]:
            ordered.append(end)
        biggest = max(
            (ordered[i + 1] - ordered[i]).total_seconds() / 3600
            for i in range(len(ordered) - 1)
        )
        out["ops.stalled_hours"] = _Value(biggest, len(ordered))
    else:
        out["ops.stalled_hours"] = _Value(no_data_reason="Too few recorded events to measure")

    # ── incidents, trucks and vessel together ──
    incidents = int(marine.get("marine.safety_incidents", _Value()).value or 0)
    incidents += sum(1 for t in d.truck_ops if (_f(t.spillage_mt) or 0) > 0)
    incidents += sum(1 for a in d.audits if a.result == AuditResult.not_satisfactory)
    out["ops.safety_incidents"] = _Value(float(incidents), len(d.truck_ops) + len(d.activities))

    return out


def _finance_metrics(d: _Data, op: Operation) -> Dict[str, _Value]:
    out: Dict[str, _Value] = {}

    # Voucher has no submitted_at column; created_at is the closest honest
    # proxy for when it entered Finance's queue.
    waits = [
        w for w in (_hours(v.created_at, v.approved_at) for v in d.vouchers if v.approved_at)
        if w is not None
    ]
    out["finance.voucher_turnaround_hours"] = (
        _Value(_mean(waits), len(waits)) if waits
        else _Value(no_data_reason="No vouchers approved on this operation")
    )

    if op.completed_at and d.invoices:
        first_invoice = min(_aware(i.created_at) for i in d.invoices)
        out["finance.invoice_issuance_hours"] = _Value(
            _hours(op.completed_at, first_invoice), len(d.invoices)
        )
    elif op.completed_at:
        out["finance.invoice_issuance_hours"] = _Value(
            no_data_reason="Operation completed but no invoice raised yet"
        )
    else:
        out["finance.invoice_issuance_hours"] = _Value(
            no_data_reason="Operation not completed yet"
        )

    went_active = next(
        (h.created_at for h in d.history if h.to_status == OperationStatus.active), None
    )
    if not d.allocations:
        # Not a failing. Finance is deliberately decoupled in this system and
        # never gates an operation's progress, so plenty of legitimate
        # operations carry no allocation at all. Scoring that zero would mark
        # Finance "Unsatisfactory" on almost every operation.
        out["finance.allocation_coverage_pct"] = _Value(
            no_data_reason="No PFI allocation linked — finance never gates an operation, so this is not counted against anyone"
        )
    elif went_active:
        in_time = [a for a in d.allocations if _aware(a.created_at) <= _aware(went_active)]
        out["finance.allocation_coverage_pct"] = _Value(
            len(in_time) / len(d.allocations) * 100.0, len(d.allocations)
        )
    else:
        out["finance.allocation_coverage_pct"] = _Value(100.0, len(d.allocations))

    return out


def _regulatory_metrics(d: _Data, op: Operation) -> Dict[str, _Value]:
    links = list(op.naval_clearances or [])
    if not links:
        # Clearances are optional and linkable at any time — the system
        # treats them as never gating an operation. An operation without one
        # is therefore unmeasured here, not failed. If the BM wants absence
        # to count as a breach, that is a policy change, not a code default.
        return {"regulatory.clearance_readiness_pct": _Value(
            no_data_reason="No naval clearance linked to this operation")}

    # The clearance has to be in place before the first fuel figure is
    # recorded — that is what "cleared for loading" means in practice.
    first_loading = [
        _aware(t.loading_quantity_recorded_at) for t in d.truck_ops
        if t.loading_quantity_recorded_at
    ] + [
        _aware(a.loading_quantity_recorded_at) for a in d.activities
        if a.loading_quantity_recorded_at
    ]
    if not first_loading:
        return {"regulatory.clearance_readiness_pct": _Value(100.0, len(links))}

    earliest_loading = min(first_loading)
    in_time = sum(1 for link in links if _aware(link.created_at) <= earliest_loading)
    return {"regulatory.clearance_readiness_pct": _Value(
        in_time / len(links) * 100.0, len(links))}


# ── assembly ──────────────────────────────────────────────────────────────

def _build_metric_out(metric: MetricDef, computed: Optional[_Value],
                      override: Optional[_Override]) -> MetricOut:
    target = override.target if override and override.target is not None else metric.target
    fail = override.fail if override and override.fail is not None else metric.fail
    weight = override.weight if override and override.weight is not None else metric.weight

    value = computed.value if computed else None
    score = score_value(metric, value, target=target, fail=fail)

    return MetricOut(
        key=metric.key,
        label=metric.label,
        unit=metric.unit,
        value=value,
        score=score,
        rating=rating_for(score),
        target=target,
        fail=fail,
        weight=weight,
        critical=metric.critical,
        source=metric.source.value,
        doc=metric.doc,
        description=metric.description,
        sample_size=computed.sample_size if computed else None,
        no_data_reason=(computed.no_data_reason if computed else _NOT_RECORDED) if value is None else None,
        target_overridden=bool(override and override.target is not None),
    )


# The roles a scorecard reports on. Bunker Manager is deliberately absent as
# a separate block: it shares its metric set with Ops Supervisor, and showing
# the same numbers twice would be noise.
_SCORECARD_ROLES = (
    UserRole.ops_supervisor,
    UserRole.cargo_superintendent,
    UserRole.logistics_officer,
    UserRole.finance_manager,
    UserRole.marine_operator,
)


class ScorecardService:

    @staticmethod
    async def get_operation_scorecard(operation_id, current_user: User,
                                      db: AsyncSession) -> OperationScorecardOut:
        stmt = select(Operation).where(
            Operation.id == operation_id, Operation.deleted_at.is_(None)
        )
        operation = (await db.execute(stmt)).scalars().first()
        if not operation:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                                detail="Operation not found")

        # Visibility is enforced by the caller (the router applies the same
        # rule the rest of the operation routes use) so this stays a pure
        # computation and can be reused by the monthly rollup later.
        #
        # Passed around explicitly rather than assigned onto
        # `operation.task_assignments`: setting an un-loaded relationship
        # makes SQLAlchemy lazy-load the previous value first, which raises
        # MissingGreenlet under an async session.
        assignments = (await db.execute(
            select(TaskAssignment).where(TaskAssignment.operation_id == operation_id)
        )).scalars().all()

        overrides = await resolve_overrides(db)
        data = await _gather(operation, assignments, db)

        truck = _truck_metrics(data, operation)
        marine = _vessel_metrics(data, operation)
        values: Dict[str, _Value] = {}
        values.update(truck)
        values.update(marine)
        values.update(_ops_metrics(data, operation, truck, marine))
        values.update(_finance_metrics(data, operation))
        values.update(_regulatory_metrics(data, operation))

        roles: List[RoleScoreOut] = []
        for role in _SCORECARD_ROLES:
            metric_outs = [
                _build_metric_out(m, values.get(m.key), overrides.get(m.key))
                for m in metrics_for_role(role)
            ]
            if not metric_outs:
                continue
            role_score = weighted_score([(m.weight, m.score) for m in metric_outs])
            roles.append(RoleScoreOut(
                role=role.value,
                role_label=role_label(role),
                score=role_score,
                rating=rating_for(role_score),
                metrics=metric_outs,
            ))

        # The operation score is the plain mean of the role scores that had
        # anything to measure. Weighting roles against each other would be a
        # judgment about whose work matters more, which is the BM's call to
        # make explicitly, not something to bury in a default.
        overall = _mean([r.score for r in roles])

        return OperationScorecardOut(
            operation_id=operation.id,
            operation_number=operation.operation_number,
            operation_type=operation.type.value if operation.type else None,
            status=operation.status.value if operation.status else None,
            computed_at=datetime.now(timezone.utc),
            overall_score=overall,
            overall_rating=rating_for(overall),
            roles=roles,
            contributors=_contributors(data, assignments),
        )


def _contributors(d: _Data, assignments) -> List[ContributorOut]:
    by_id = {u.id: u for u in d.people}
    involvement: Dict = {}

    for ta in assignments:
        involvement.setdefault(ta.assigned_to, set()).add(
            f"Task: {ta.task_type.value.replace('_', ' ').title()}"
        )
    for a in d.activities:
        if a.assigned_to:
            involvement.setdefault(a.assigned_to, set()).add("Vessel run")
    for t in d.truck_ops:
        if t.logged_by:
            involvement.setdefault(t.logged_by, set()).add("Truck records")

    out: List[ContributorOut] = []
    for user_id, roles_involved in involvement.items():
        user = by_id.get(user_id)
        if not user:
            continue
        out.append(ContributorOut(
            user_id=user_id,
            full_name=user.full_name,
            role=user.role.value,
            role_label=role_label(user.role),
            involvement=sorted(roles_involved),
        ))
    return sorted(out, key=lambda c: (c.role_label, c.full_name or ""))
