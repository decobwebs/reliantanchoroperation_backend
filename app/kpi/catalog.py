"""The KPI metric catalog — every measure the system scores, the job document
its target came from, and how a raw value becomes a 0-100 score.

Targets here are DEFAULTS, taken from Reliant Anchor's own job-responsibility
documents (each metric names its source in `doc`). They are not the last
word: a row in `kpi_targets` overrides any of them from its effective_from
date onward, so the Bunker Manager retunes without a deploy. Calling code
must always resolve through `resolve_targets()` and never read
`MetricDef.target` directly.

Policy, decided by the BM on 2026-08-25: **strictest from each document**.
Where two documents state different targets for what is arguably the same
measure, the tighter one is used. The clearest case is cargo loss, which the
documents put at 0.006%, 1%, 1.5% and 2% in four places — each role here
carries the tightest figure its own document states. This was a deliberate
choice made with the knowledge that first-month scores will look harsh.

`fail` is the value at which a metric scores zero. It is not from the
documents — the documents state targets, not failure points — so these are
starting estimates chosen to keep each curve informative across the range
real operations actually produce. They are the first thing to tune once
there is a month of data.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from app.models.enums import UserRole


class Curve(str, Enum):
    """How a raw value maps to a 0-100 score."""

    # Score 100 at or below target, 0 at or above fail, straight line between.
    # Losses, delays, incident counts — anything where less is better.
    lower_better = "lower_better"

    # Mirror image: 100 at or above target, 0 at or below fail. Runway days,
    # coverage where the figure is a stock rather than a rate.
    higher_better = "higher_better"

    # The value is already a percentage that means "how often this went
    # right", so it IS the score. Clamped to 0-100.
    rate = "rate"


class MetricSource(str, Enum):
    auto = "auto"       # computed from records the system already holds
    graded = "graded"   # a human judgment, entered with a written reason


@dataclass(frozen=True)
class MetricDef:
    key: str                  # stable forever; used as the kpi_targets join key
    label: str                # what a person reads on screen
    role: UserRole            # whose score this belongs to
    unit: str                 # "%", "L", "hours", "count" — drives formatting
    curve: Curve
    target: float
    fail: float
    weight: float             # share of the role's score; weights per role sum to 1.0
    doc: str                  # provenance: which job document, which KPI on it
    description: str
    critical: bool = False    # the document marks it CRITICAL
    source: MetricSource = MetricSource.auto


# ── Marine Operations (cargo_superintendent) ──────────────────────────────
# Source document: "RAL CARGO SUPERINTENDENT RESPONSIBILITIES 2026".
_MARINE: List[MetricDef] = [
    MetricDef(
        key="marine.cargo_loss_pct",
        label="Cargo loss",
        role=UserRole.cargo_superintendent,
        unit="%",
        curve=Curve.lower_better,
        target=1.0, fail=3.0, weight=0.28, critical=True,
        doc="Cargo Superintendent — KPI 1, Cargo Loss Control (CRITICAL): target ≤ 1% loss per operation",
        description="Variance between what the barge discharged and what the receiving vessel recorded, as a percentage of the discharged figure.",
    ),
    MetricDef(
        key="marine.bdn_first_pass_pct",
        label="BDN accepted first time",
        role=UserRole.cargo_superintendent,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.14,
        doc="Cargo Superintendent — KPI 2, Quantity & Quality Accuracy: target 100% verification accuracy",
        description="Share of this run's Vessel BDNs approved without being rejected and resubmitted.",
    ),
    MetricDef(
        key="marine.reporting_lag_hours",
        label="Reporting lag",
        role=UserRole.cargo_superintendent,
        unit="hours",
        curve=Curve.lower_better,
        target=1.0, fail=12.0, weight=0.14,
        doc="Cargo Superintendent — KPI 3, Operational Reporting Timeliness: target 100% real-time updates, measured by time lag in reporting",
        description="Average gap between when a stage actually happened and when it was entered — the two-clock measure, from the stage tables' paired user/system timestamps.",
    ),
    MetricDef(
        key="marine.hse_completion_pct",
        label="HSE checks completed",
        role=UserRole.cargo_superintendent,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.14,
        doc="Cargo Superintendent — KPI 5, Safety & Spill Control: target zero safety incidents",
        description="Share of required HSE checks recorded. A full-operation run needs three (pre, during, post); each delivery leg needs its own.",
    ),
    MetricDef(
        key="marine.documentation_pct",
        label="Quantity figures complete",
        role=UserRole.cargo_superintendent,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.14,
        doc="Cargo Superintendent — KPI 4, Documentation Compliance: target zero missing or incomplete documentation",
        description="Share of completed runs and legs carrying a full measurement chain — GOV, VCF, GSV, MT(vac) and density all recorded.",
    ),
    MetricDef(
        key="marine.safety_incidents",
        label="Safety incidents",
        role=UserRole.cargo_superintendent,
        unit="count",
        curve=Curve.lower_better,
        target=0.0, fail=3.0, weight=0.08,
        doc="Cargo Superintendent — KPI 5, Safety & Spill Control: target zero safety incidents",
        description="Recorded spillages plus HSE checks returned not-satisfactory.",
    ),
    MetricDef(
        key="marine.conduct_graded",
        label="Conduct, vigilance and communication",
        role=UserRole.cargo_superintendent,
        unit="score",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.08,
        source=MetricSource.graded,
        doc="Cargo Superintendent — KPI 6, Pilferage Prevention and KPI 7, Communication Efficiency",
        description="Judged, not counted: crew supervision, pilferage vigilance, and whether issues were escalated in time. Entered by the Bunker Manager with a written reason.",
    ),
]

# ── Truck Operation (logistics_officer) ───────────────────────────────────
# Source documents: "RAL TRUCK SUPERVISOR RESPONSIBILITIES 2026",
# "RAL TRUCK OPERATORS JOB RESPONSIBILITIES 2026",
# "RAL REFINER, DEPOT REPRESENTATIVE JOB RESPONSIBILITIES 2026".
# All three map to this one RAOMS role, so each measure takes the tightest
# figure any of them states.
_TRUCK: List[MetricDef] = [
    MetricDef(
        key="truck.loss_litres_per_truck",
        label="Loss per truck",
        role=UserRole.logistics_officer,
        unit="L",
        curve=Curve.lower_better,
        target=1500.0, fail=3000.0, weight=0.28, critical=True,
        doc="Truck Supervisor — KPI 5, Loss Control: strict maximum 1,500 litres per truck",
        description="Average litres lost per truck between loading and discharge. Trucks stay in litres, never MT.",
    ),
    MetricDef(
        key="truck.discharge_hours",
        label="Discharge time per truck",
        role=UserRole.logistics_officer,
        unit="hours",
        curve=Curve.lower_better,
        target=1.0, fail=4.0, weight=0.18, critical=True,
        doc="Truck Operators — KPI 1, Discharge Efficiency (CRITICAL): target all trucks discharged within ≤ 1 hour each",
        description="Average time from discharge start to discharge end, per truck.",
    ),
    MetricDef(
        key="truck.mobilization_hours",
        label="Mobilization to loading point",
        role=UserRole.logistics_officer,
        unit="hours",
        curve=Curve.lower_better,
        target=48.0, fail=96.0, weight=0.0675,
        doc="Truck Supervisor — KPI 1, Truck Mobilization Efficiency: target 100% truck arrival within 48 hours",
        description="Time from a truck being sourced onto the operation to it arriving at the loading point.",
    ),
    MetricDef(
        key="truck.loading_turnaround_hours",
        label="Loading turnaround",
        role=UserRole.logistics_officer,
        unit="hours",
        curve=Curve.lower_better,
        target=24.0, fail=72.0, weight=0.0675,
        doc="Refinery / Depot Representative — KPI 2, Truck Turnaround Efficiency: loading completed within 24 hours of truck arrival",
        description="Time from arriving at the loading point to departing it loaded.",
    ),
    MetricDef(
        key="truck.on_time_delivery_pct",
        label="On-time delivery",
        role=UserRole.logistics_officer,
        unit="%",
        curve=Curve.rate,
        target=95.0, fail=0.0, weight=0.0675,
        doc="Truck Operations Manager — KPI 5, Truck Delivery Performance: target 95% on-time deliveries, measured Planned vs Actual Arrival Times",
        description="Share of trucks that reached the discharge point by their planned arrival time. Only trucks given a planned arrival are counted — a truck with no plan is not scored as late.",
    ),
    MetricDef(
        key="truck.depart_after_loading_hours",
        label="Departure after loading",
        role=UserRole.logistics_officer,
        unit="hours",
        curve=Curve.lower_better,
        target=8.0, fail=24.0, weight=0.0675,
        doc="Truck Supervisor — KPI 2, Loading-to-Movement Time: all trucks depart within 8 hours of loading",
        description="Time from leaving the loading point to actually starting transit.",
    ),
    MetricDef(
        key="truck.figure_gap_pct",
        label="Figure accuracy",
        role=UserRole.logistics_officer,
        unit="%",
        curve=Curve.lower_better,
        target=0.5, fail=5.0,
        weight=0.0675,
        doc="Blueprint signature metric — the Data Trust Score. Not from a job document: derived from the system_* shadow columns RAOMS already captures on every Truck BDN precisely so submitted figures can be checked against recorded ones.",
        description="Average gap between the quantities a person submits on a Truck BDN and the quantities the system had already recorded for the same trucks. Measures whether the paperwork can be trusted, not whether the operation went well.",
    ),
    MetricDef(
        key="truck.safety_audit_pct",
        label="Safety audits complete",
        role=UserRole.logistics_officer,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.0675,
        doc="Truck Operators — KPI 6, Safety Compliance: target zero safety incidents during discharge",
        description="Share of trucks carrying both a Pre and a Post safety audit, each returned satisfactory.",
    ),
    MetricDef(
        key="truck.bdn_first_pass_pct",
        label="Truck BDN accepted first time",
        role=UserRole.logistics_officer,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.0675,
        doc="Refinery / Depot Representative — KPI 4, Documentation Accuracy: target 100% complete and error-free documentation",
        description="Share of this operation's Truck BDNs approved without being rejected and resubmitted.",
    ),
    MetricDef(
        key="truck.vigilance_graded",
        label="Vigilance and vendor handling",
        role=UserRole.logistics_officer,
        unit="score",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.0675,
        source=MetricSource.graded,
        doc="Truck Operators — KPI 5, Leakage & Irregularity Detection; Truck Supervisor — KPI 6, Vendor Performance Management",
        description="Judged, not counted: whether leaks and irregularities were spotted before anyone else found them, and how vendor problems were handled. Entered with a written reason.",
    ),
]

# ── Operation managers (bunker_manager, ops_supervisor) ───────────────────
# Source documents: "RELIANT ANCHOR LOGISTICS Operations Manager JOB
# RESPONSIBILITIES 2026" and "RELIANT ANCHOR LOGISTICS LTD Marine & Cargo
# Assistant 2026". The Marine & Cargo Assistant's 0.006% variance target is
# the strictest figure anywhere in the eleven documents and is applied here,
# per the strictest-from-each-document policy.
_OPS: List[MetricDef] = [
    MetricDef(
        key="ops.operation_loss_pct",
        label="Operation loss",
        role=UserRole.ops_supervisor,
        unit="%",
        curve=Curve.lower_better,
        target=2.0, fail=5.0, weight=0.24, critical=True,
        doc="Operations Manager — KPI 1, Product Loss Control (CRITICAL): target ≤ 2% loss per operation",
        description="Whole-operation variance: everything loaded against everything delivered, across trucks and vessel runs together.",
    ),
    MetricDef(
        key="ops.vessel_variance_pct",
        label="Vessel transfer variance",
        role=UserRole.ops_supervisor,
        unit="%",
        curve=Curve.lower_better,
        target=0.006, fail=1.0, weight=0.18, critical=True,
        doc="Marine & Cargo Assistant — KPI 1, Cargo Loss Control (CRITICAL): target ≤ 0.006% variance per vessel operation",
        description="Barge-to-vessel variance measured on its own, held to the tightest target in the company's documents.",
    ),
    MetricDef(
        key="ops.mobilization_response_hours",
        label="Mobilization response",
        role=UserRole.ops_supervisor,
        unit="hours",
        curve=Curve.lower_better,
        target=4.0, fail=24.0, weight=0.14,
        doc="Operations Manager — KPI 3, Operational Response Time: truck nomination within 2-4 hours of PFI",
        description="Time from the operation being created to staff being assigned and it leaving Draft.",
    ),
    MetricDef(
        key="ops.approval_turnaround_hours",
        label="Approval turnaround",
        role=UserRole.ops_supervisor,
        unit="hours",
        curve=Curve.lower_better,
        target=12.0, fail=48.0, weight=0.18,
        doc="Marine & Cargo Assistant — KPI 6, Marine Coordination Efficiency: target zero avoidable delays between teams. The 12-hour figure is an SLA to confirm with the BM — no document states one.",
        description="Average time a submitted BDN or readiness feedback waits for approval or rejection.",
    ),
    MetricDef(
        key="ops.stalled_hours",
        label="Longest silence on the operation",
        role=UserRole.ops_supervisor,
        unit="hours",
        curve=Curve.lower_better,
        target=24.0, fail=96.0, weight=0.09,
        doc="Operations Manager — KPI 6, Reporting Accuracy & Timeliness: target 100% daily and operational reporting compliance",
        description="The longest gap between any two recorded events while the operation was active. Daily reporting means this should stay under 24 hours.",
    ),
    MetricDef(
        key="ops.safety_incidents",
        label="Spill and safety incidents",
        role=UserRole.ops_supervisor,
        unit="count",
        curve=Curve.lower_better,
        target=0.0, fail=3.0, weight=0.09,
        doc="Operations Manager — KPI 5, Spill & Safety Compliance: target zero major spill incidents",
        description="Every recorded spillage and failed safety check across the whole operation, trucks and vessel alike.",
    ),
    MetricDef(
        key="ops.leadership_graded",
        label="Coordination and escalation",
        role=UserRole.ops_supervisor,
        unit="score",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.08,
        source=MetricSource.graded,
        doc="Operations Manager — KPI 6, Reporting Accuracy & Timeliness; Marine & Cargo Assistant — KPI 6, Marine Coordination Efficiency",
        description="Judged, not counted: how clearly the operation was run across teams and how quickly risks were escalated. Entered with a written reason.",
    ),
]

# ── Finance Manager ───────────────────────────────────────────────────────
# Source document: no finance-specific job document was supplied. These
# follow the finance lifecycle already modelled in the system; targets are
# starting proposals for the BM to confirm, not quotations from a document.
_FINANCE: List[MetricDef] = [
    MetricDef(
        key="finance.voucher_turnaround_hours",
        label="Voucher turnaround",
        role=UserRole.finance_manager,
        unit="hours",
        curve=Curve.lower_better,
        target=48.0, fail=168.0, weight=0.30,
        doc="No source document supplied — proposed target, awaiting BM confirmation",
        description="Time from a payment voucher being submitted to it being approved or rejected.",
    ),
    MetricDef(
        key="finance.invoice_issuance_hours",
        label="Invoice issued after completion",
        role=UserRole.finance_manager,
        unit="hours",
        curve=Curve.lower_better,
        target=72.0, fail=240.0, weight=0.40,
        doc="No source document supplied — proposed target, awaiting BM confirmation",
        description="Time from the operation completing to the client invoice being raised.",
    ),
    MetricDef(
        key="finance.allocation_coverage_pct",
        label="PFI allocation in place",
        role=UserRole.finance_manager,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.30,
        doc="No source document supplied — proposed target, awaiting BM confirmation",
        description="Whether the operation carried a PFI allocation before it went Active.",
    ),
]

# ── Marine Operator (regulatory paperwork) ────────────────────────────────
# Source document: "RAL Chartering & Marine Admin Officer Job
# Responsibilities 2026". Its licence-runway and waiver-pool measures are
# stock levels rather than per-operation events, so they arrive with the
# monthly rollup in a later phase; only the per-operation measure is here.
_REGULATORY: List[MetricDef] = [
    MetricDef(
        key="regulatory.clearance_readiness_pct",
        label="Clearance in place before loading",
        role=UserRole.marine_operator,
        unit="%",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.70, critical=True,
        doc="Chartering & Marine Admin Officer — KPI 6, Regulatory Compliance: target zero regulatory issues; and KPI 4, Demurrage Prevention (CRITICAL)",
        description="Whether a naval clearance was linked to the operation before the first loading figure was recorded against it.",
    ),
    MetricDef(
        key="regulatory.permit_delays_graded",
        label="Permit delays avoided",
        role=UserRole.marine_operator,
        unit="score",
        curve=Curve.rate,
        target=100.0, fail=0.0, weight=0.30,
        source=MetricSource.graded,
        doc="Chartering & Marine Admin Officer — KPI 6, Regulatory Compliance: target zero regulatory issues or penalties",
        description="Judged, not counted: whether any operation was held up waiting on paperwork this period. Entered with a written reason.",
    ),
]


CATALOG: Dict[str, MetricDef] = {
    m.key: m
    for m in (_MARINE + _TRUCK + _OPS + _FINANCE + _REGULATORY)
}

# ops_supervisor and bunker_manager are measured on the same set — the Ops
# Supervisor holds the BM's authority inside operations, so they answer for
# the same outcomes. Keyed separately so a lookup by either role works.
_BY_ROLE: Dict[UserRole, List[MetricDef]] = {}
for _m in CATALOG.values():
    _BY_ROLE.setdefault(_m.role, []).append(_m)
_BY_ROLE[UserRole.bunker_manager] = list(_BY_ROLE.get(UserRole.ops_supervisor, []))


def metrics_for_role(role: UserRole) -> List[MetricDef]:
    return list(_BY_ROLE.get(role, []))


# ── Scoring ───────────────────────────────────────────────────────────────

# The rating bands ten of the eleven job documents share verbatim. (The Truck
# Operations Manager document uses a five-band variant — 90+/80+/70+/60+ —
# which is not used here; one scale across the company keeps scores
# comparable, and this is the one nearly every document states.)
RATING_BANDS = (
    (90.0, "Excellent"),
    (75.0, "Good"),
    (60.0, "Needs Improvement"),
    (0.0, "Unsatisfactory"),
)


def rating_for(score: Optional[float]) -> Optional[str]:
    """The company's own words for a score. None in, None out — an unmeasured
    metric has no rating, and must never be presented as 'Unsatisfactory'."""
    if score is None:
        return None
    for floor, label in RATING_BANDS:
        if score >= floor:
            return label
    return "Unsatisfactory"


def score_value(metric: MetricDef, value: Optional[float],
                target: Optional[float] = None,
                fail: Optional[float] = None) -> Optional[float]:
    """Turn one raw measurement into a 0-100 score.

    Returns None when there is nothing to score. That is a real and common
    state — a truck-only operation has no vessel readings — and it must stay
    distinct from a zero, which means "measured, and bad". Callers drop None
    metrics from the weighted mean rather than counting them as failures.

    `target`/`fail` override the catalog defaults and are how kpi_targets
    rows take effect.
    """
    if value is None:
        return None

    lo = metric.target if target is None else target
    hi = metric.fail if fail is None else fail

    if metric.curve is Curve.rate:
        return max(0.0, min(100.0, float(value)))

    if metric.curve is Curve.lower_better:
        if value <= lo:
            return 100.0
        if value >= hi:
            return 0.0
        # hi > lo is guaranteed here by the two comparisons above.
        return 100.0 * (hi - value) / (hi - lo)

    # higher_better
    if value >= lo:
        return 100.0
    if value <= hi:
        return 0.0
    return 100.0 * (value - hi) / (lo - hi)


def weighted_score(scored: List[tuple]) -> Optional[float]:
    """Roll a list of (weight, score) pairs into one score.

    Pairs whose score is None are dropped and the remaining weights are
    renormalized, so an operation that never involved a barge is not punished
    for having no vessel figures. If nothing was measurable at all, the result
    is None rather than 0.
    """
    usable = [(w, s) for w, s in scored if s is not None and w > 0]
    if not usable:
        return None
    total_weight = sum(w for w, _ in usable)
    if total_weight <= 0:
        return None
    return sum(w * s for w, s in usable) / total_weight
