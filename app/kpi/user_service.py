"""Per-person KPI scoring (Phase 2).

THE ATTRIBUTION RULE — the decision this whole phase turns on.

Phase 1 scores operations. Scoring *people* means deciding who owns which
number, and there were two ways to do it:

  (a) score the operation, then split it among everyone who touched it;
  (b) score each person on the records they personally created.

This uses (b). Under (a) a careful truck officer would be dragged down by a
vessel run they never saw, and nobody could answer "why is my score 71?"
without unpicking someone else's work. Under (b) every number a person is
shown traces back to rows carrying their own user id, so a disputed score is
settled by opening the records — which is the same standard the rest of RAOMS
holds itself to.

Who owns what, by the record that names them:

    truck movement, loss, turnaround   truck_operations.logged_by
    truck safety audits                truck_safety_audits.conducted_by
    Truck BDN accuracy                 truck_bdns.generated_by
    vessel HSE and incidents           vessel_activities.assigned_to
    leg reporting lag, figure chains   the parent run's assigned_to
    vessel cargo loss, BDN accuracy    bdns.generated_by
    approval turnaround                reviewed_by on whatever was approved
    operation-level loss and response  operations.created_by
    voucher turnaround                 vouchers.approved_by
    invoice issuance                   invoices.generated_by

Operation-level metrics go to the operation's creator because somebody has to
own them and the creator is the manager who set it up. That is the least
arbitrary choice available, not an obviously right one — it is written into
the response as `attribution` on every metric so the person can see why a
number is theirs, and it is the first thing to revisit if the BM disagrees.

Everything is computed for ALL users at once in about ten grouped queries,
because the leaderboard needs the same figures as the personal page and the
database is a continent away. Never add a per-user query.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.catalog import (
    CATALOG, MetricDef, metrics_for_role, rating_for, score_value, weighted_score,
)
from app.kpi.service import _f, resolve_overrides
from app.models.enums import UserRole, role_label

logger = logging.getLogger("raoms")


# Which record each metric is drawn from, shown to the person being scored.
ATTRIBUTION: Dict[str, str] = {
    "truck.loss_litres_per_truck": "Trucks you recorded",
    "truck.discharge_hours": "Trucks you recorded",
    "truck.mobilization_hours": "Trucks you recorded",
    "truck.loading_turnaround_hours": "Trucks you recorded",
    "truck.depart_after_loading_hours": "Trucks you recorded",
    "truck.on_time_delivery_pct": "Trucks you recorded that had a planned arrival",
    "truck.safety_audit_pct": "Safety audits you conducted",
    "truck.bdn_first_pass_pct": "Truck BDNs you submitted",
    "truck.figure_gap_pct": "Truck BDNs you submitted, vs what the system recorded",
    "marine.hse_completion_pct": "Vessel runs assigned to you",
    "marine.safety_incidents": "Vessel runs assigned to you",
    "marine.reporting_lag_hours": "Delivery legs on your vessel runs",
    "marine.documentation_pct": "Delivery legs on your vessel runs",
    "marine.cargo_loss_pct": "Vessel BDNs you submitted",
    "marine.bdn_first_pass_pct": "Vessel BDNs you submitted",
    "ops.approval_turnaround_hours": "Items you approved or rejected",
    "ops.operation_loss_pct": "Operations you created",
    "ops.mobilization_response_hours": "Operations you created",
    "finance.voucher_turnaround_hours": "Vouchers you approved",
    "finance.invoice_issuance_hours": "Invoices you raised",
    # Graded, not counted. The grader and their written reason travel with
    # the score, so a judgment is never delivered anonymously.
    "marine.conduct_graded": "Graded judgment",
    "truck.vigilance_graded": "Graded judgment",
    "ops.leadership_graded": "Graded judgment",
    "regulatory.permit_delays_graded": "Graded judgment",
}

# How much of a role's total metric weight must have something recorded
# before a score is published. See score_user for why this exists — without
# it, one measurement outranks six. A starting figure, not a documented one;
# tune it once there is a full month of data.
MIN_COVERAGE = 0.5

# Metrics deliberately NOT scored per person: they describe an operation
# rather than anyone's own work, and inventing an owner for them would make
# scores unexplainable. They stay on the operation scorecard.
NOT_PER_PERSON = {
    "ops.vessel_variance_pct",
    "ops.stalled_hours",
    "ops.safety_incidents",
    "finance.allocation_coverage_pct",
    "regulatory.clearance_readiness_pct",
}


def period_bounds(period: str) -> Tuple[datetime, datetime]:
    """'YYYY-MM' -> [start, end) in UTC."""
    year, month = (int(p) for p in period.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
           else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    return start, end


def current_period(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _put(store: Dict, user_id, key: str, value, sample) -> None:
    """Record one metric value for one user, if there is anything to record."""
    if user_id is None or value is None:
        return
    store.setdefault(user_id, {})[key] = (float(value), int(sample or 0))


# ── the queries ───────────────────────────────────────────────────────────
# Each returns one row per user. Filtered to the period, grouped by whoever
# the attribution rule above names.

_Q_TRUCKS = text("""
    SELECT logged_by AS user_id,
      avg(abs(quantity_loaded_mt - quantity_discharged_mt))
        FILTER (WHERE quantity_loaded_mt IS NOT NULL
                  AND quantity_discharged_mt IS NOT NULL)              AS loss,
      count(*) FILTER (WHERE quantity_loaded_mt IS NOT NULL
                         AND quantity_discharged_mt IS NOT NULL)       AS loss_n,
      avg(EXTRACT(EPOCH FROM (discharge_end_at - discharge_start_at))/3600.0)
        FILTER (WHERE discharge_end_at >= discharge_start_at)          AS disch,
      count(*) FILTER (WHERE discharge_end_at >= discharge_start_at)   AS disch_n,
      avg(EXTRACT(EPOCH FROM (arrived_loading_at - created_at))/3600.0)
        FILTER (WHERE arrived_loading_at >= created_at)                AS mob,
      count(*) FILTER (WHERE arrived_loading_at >= created_at)         AS mob_n,
      avg(EXTRACT(EPOCH FROM (departed_loading_at - arrived_loading_at))/3600.0)
        FILTER (WHERE departed_loading_at >= arrived_loading_at)       AS turn,
      count(*) FILTER (WHERE departed_loading_at >= arrived_loading_at) AS turn_n,
      avg(EXTRACT(EPOCH FROM (transit_start_at - departed_loading_at))/3600.0)
        FILTER (WHERE transit_start_at >= departed_loading_at)         AS dep,
      count(*) FILTER (WHERE transit_start_at >= departed_loading_at)  AS dep_n,
      -- On-time delivery. Only trucks that were GIVEN a plan and have
      -- actually arrived are counted, so a truck nobody planned for is left
      -- unmeasured rather than scored as a miss.
      count(*) FILTER (WHERE expected_arrival_at IS NOT NULL
                         AND arrived_discharge_at IS NOT NULL)          AS ontime_n,
      count(*) FILTER (WHERE expected_arrival_at IS NOT NULL
                         AND arrived_discharge_at IS NOT NULL
                         AND arrived_discharge_at <= expected_arrival_at) AS ontime_ok
    FROM truck_operations
    WHERE logged_by IS NOT NULL AND created_at >= :s AND created_at < :e
    GROUP BY logged_by
""")

_Q_AUDITS = text("""
    SELECT conducted_by AS user_id, count(*) AS n,
           count(*) FILTER (WHERE result = 'satisfactory') AS ok
    FROM truck_safety_audits
    WHERE conducted_by IS NOT NULL AND conducted_at >= :s AND conducted_at < :e
    GROUP BY conducted_by
""")

_Q_TRUCK_BDNS = text("""
    -- The Data Trust Score. RAOMS snapshots what it had already recorded into
    -- the system_* columns at the moment a BDN is submitted, precisely so the
    -- two can be compared. `gap` averages the absolute percentage difference
    -- across the loaded and discharged figures, per BDN, then across BDNs.
    --
    -- Both sides must be present and the system side non-zero, or the pair is
    -- skipped — dividing by an absent baseline would invent a discrepancy.
    SELECT b.generated_by AS user_id,
           count(*) FILTER (WHERE b.status::text IN ('approved','rejected')) AS decided,
           count(*) FILTER (WHERE b.status::text = 'approved'
                              AND coalesce(b.rejection_reason,'') = '')      AS clean,
           avg(g.gap)   AS gap,
           count(g.gap) AS gap_n
    FROM truck_bdns b
    LEFT JOIN LATERAL (
        SELECT avg(d.pct) AS gap FROM (
            SELECT abs(b.quantity_loaded_mt - b.system_quantity_loaded_mt)
                   / nullif(b.system_quantity_loaded_mt, 0) * 100.0 AS pct
            UNION ALL
            SELECT abs(b.quantity_discharged_mt - b.system_quantity_discharged_mt)
                   / nullif(b.system_quantity_discharged_mt, 0) * 100.0
        ) d WHERE d.pct IS NOT NULL
    ) g ON TRUE
    WHERE b.generated_by IS NOT NULL AND b.created_at >= :s AND b.created_at < :e
    GROUP BY b.generated_by
""")

_Q_RUNS = text("""
    SELECT assigned_to AS user_id, count(*) AS runs,
      sum( (CASE WHEN hse_conducted_at        IS NOT NULL THEN 1 ELSE 0 END)
         + (CASE WHEN hse_during_conducted_at IS NOT NULL THEN 1 ELSE 0 END)
         + (CASE WHEN hse_post_conducted_at   IS NOT NULL THEN 1 ELSE 0 END) ) AS hse_done,
      count(*) FILTER (WHERE coalesce(spillage_mt, 0) > 0)                     AS spills,
      sum( (CASE WHEN hse_result::text        = 'not_satisfactory' THEN 1 ELSE 0 END)
         + (CASE WHEN hse_during_result::text = 'not_satisfactory' THEN 1 ELSE 0 END)
         + (CASE WHEN hse_post_result::text   = 'not_satisfactory' THEN 1 ELSE 0 END) ) AS bad_hse
    FROM vessel_activities
    WHERE assigned_to IS NOT NULL AND status::text <> 'cancelled'
      AND created_at >= :s AND created_at < :e
    GROUP BY assigned_to
""")

# The two-clock measure. Each leg carries four stage pairs, so the pairs are
# unnested and averaged across all of them.
_Q_LAG = text("""
    SELECT va.assigned_to AS user_id, avg(g.gap) AS lag, count(g.gap) AS lag_n
    FROM vessel_activity_legs l
    JOIN vessel_activities va ON va.id = l.vessel_activity_id
    CROSS JOIN LATERAL (VALUES
        (l.stage_cast_off_user_at,             l.stage_cast_off_system_at),
        (l.stage_alongside_user_at,            l.stage_alongside_system_at),
        (l.stage_discharge_commenced_user_at,  l.stage_discharge_commenced_system_at),
        (l.stage_discharge_completed_user_at,  l.stage_discharge_completed_system_at)
    ) AS p(u, sy)
    CROSS JOIN LATERAL (SELECT CASE
        WHEN p.u IS NOT NULL AND p.sy IS NOT NULL AND p.sy >= p.u
        THEN EXTRACT(EPOCH FROM (p.sy - p.u))/3600.0 END AS gap) g
    WHERE va.assigned_to IS NOT NULL AND l.cancelled_at IS NULL
      AND l.created_at >= :s AND l.created_at < :e
    GROUP BY va.assigned_to
""")

# Completeness of the GOV / VCF / GSV / MTvac / density chain, per leg.
_Q_CHAIN = text("""
    SELECT va.assigned_to AS user_id, count(*) AS n,
      avg( ( (CASE WHEN l.gov       IS NOT NULL THEN 1 ELSE 0 END)
           + (CASE WHEN l.vcf       IS NOT NULL THEN 1 ELSE 0 END)
           + (CASE WHEN l.gsv       IS NOT NULL THEN 1 ELSE 0 END)
           + (CASE WHEN l.mt_vacuum IS NOT NULL THEN 1 ELSE 0 END)
           + (CASE WHEN l.density   IS NOT NULL THEN 1 ELSE 0 END) ) * 20.0 ) AS pct
    FROM vessel_activity_legs l
    JOIN vessel_activities va ON va.id = l.vessel_activity_id
    WHERE va.assigned_to IS NOT NULL AND l.cancelled_at IS NULL
      AND l.created_at >= :s AND l.created_at < :e
    GROUP BY va.assigned_to
""")

_Q_VESSEL_BDNS = text("""
    SELECT generated_by AS user_id,
      avg(abs(discharge_gov - received_gov) / nullif(discharge_gov, 0) * 100.0)
        FILTER (WHERE discharge_gov IS NOT NULL AND received_gov IS NOT NULL) AS loss_pct,
      count(*) FILTER (WHERE discharge_gov IS NOT NULL
                         AND received_gov IS NOT NULL)                        AS loss_n,
      count(*) FILTER (WHERE status::text IN ('approved','rejected'))         AS decided,
      count(*) FILTER (WHERE status::text = 'approved'
                         AND coalesce(rejection_reason,'') = '')              AS clean
    FROM bdns
    WHERE generated_by IS NOT NULL AND created_at >= :s AND created_at < :e
    GROUP BY generated_by
""")

# Everything a manager approves, in one pass.
_Q_APPROVALS = text("""
    SELECT user_id, avg(h) AS turnaround, count(*) AS n FROM (
        SELECT reviewed_by AS user_id,
               EXTRACT(EPOCH FROM (approved_at - created_at))/3600.0 AS h
        FROM bdns
        WHERE reviewed_by IS NOT NULL AND approved_at IS NOT NULL
          AND approved_at >= :s AND approved_at < :e
        UNION ALL
        SELECT reviewed_by, EXTRACT(EPOCH FROM (approved_at - created_at))/3600.0
        FROM truck_bdns
        WHERE reviewed_by IS NOT NULL AND approved_at IS NOT NULL
          AND approved_at >= :s AND approved_at < :e
        UNION ALL
        SELECT reviewed_by, EXTRACT(EPOCH FROM (reviewed_at - submitted_at))/3600.0
        FROM truck_feedback
        WHERE reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
          AND reviewed_at >= :s AND reviewed_at < :e
    ) x
    WHERE h >= 0
    GROUP BY user_id
""")

_Q_OPS = text("""
    WITH per_op AS (
        SELECT o.id, o.created_by,
               sum(tr.quantity_loaded_mt)     AS loaded,
               sum(tr.quantity_discharged_mt) AS discharged
        FROM operations o
        JOIN truck_operations tr ON tr.operation_id = o.id
        WHERE o.deleted_at IS NULL AND o.created_at >= :s AND o.created_at < :e
          AND tr.quantity_loaded_mt IS NOT NULL
          AND tr.quantity_discharged_mt IS NOT NULL
        GROUP BY o.id, o.created_by
    )
    SELECT created_by AS user_id,
           avg(abs(loaded - discharged) / nullif(loaded, 0) * 100.0) AS loss_pct,
           count(*) AS n
    FROM per_op
    WHERE loaded > 0
    GROUP BY created_by
""")

_Q_MOBILIZATION = text("""
    WITH first_move AS (
        SELECT o.id, o.created_by, o.created_at,
               min(h.created_at) AS left_draft
        FROM operations o
        JOIN operation_status_history h ON h.operation_id = o.id
        WHERE o.deleted_at IS NULL AND o.created_at >= :s AND o.created_at < :e
          AND h.to_status::text <> 'draft'
        GROUP BY o.id, o.created_by, o.created_at
    )
    SELECT created_by AS user_id,
           avg(EXTRACT(EPOCH FROM (left_draft - created_at))/3600.0) AS hours,
           count(*) AS n
    FROM first_move
    WHERE left_draft >= created_at
    GROUP BY created_by
""")

_Q_FINANCE = text("""
    SELECT user_id, avg(h) AS hours, count(*) AS n, kind FROM (
        SELECT approved_by AS user_id, 'voucher' AS kind,
               EXTRACT(EPOCH FROM (approved_at - created_at))/3600.0 AS h
        FROM vouchers
        WHERE approved_by IS NOT NULL AND approved_at IS NOT NULL
          AND approved_at >= :s AND approved_at < :e
        UNION ALL
        SELECT i.generated_by, 'invoice',
               EXTRACT(EPOCH FROM (i.created_at - o.completed_at))/3600.0
        FROM invoices i
        JOIN operations o ON o.id = i.operation_id
        WHERE i.generated_by IS NOT NULL AND o.completed_at IS NOT NULL
          AND i.created_at >= :s AND i.created_at < :e
    ) x
    WHERE h >= 0
    GROUP BY user_id, kind
""")


# Latest grade per person per metric for the period. The table is
# append-only, so "latest wins" is how a revised judgment supersedes an
# earlier one without erasing it.
_Q_GRADES = text("""
    SELECT DISTINCT ON (g.user_id, g.metric_key)
           g.user_id, g.metric_key, g.score, g.reason, g.created_at,
           u.full_name AS graded_by_name
    FROM kpi_grades g
    LEFT JOIN users u ON u.id = g.graded_by
    WHERE g.period = :period AND g.superseded_at IS NULL
    ORDER BY g.user_id, g.metric_key, g.created_at DESC
""")


class UserKpiService:

    @staticmethod
    async def compute_period(db: AsyncSession, period: str) -> Dict:
        """Every user's metric values for one month.

        Returns {user_id: {metric_key: (value, sample_size)}}. One pass of
        grouped queries covering everybody — the leaderboard and the personal
        page are both served from this.
        """
        start, end = period_bounds(period)
        p = {"s": start, "e": end}
        out: Dict = {}

        async def rows(sql):
            return (await db.execute(sql, p)).mappings().all()

        for r in await rows(_Q_TRUCKS):
            u = r["user_id"]
            _put(out, u, "truck.loss_litres_per_truck", _f(r["loss"]), r["loss_n"])
            _put(out, u, "truck.discharge_hours", _f(r["disch"]), r["disch_n"])
            _put(out, u, "truck.mobilization_hours", _f(r["mob"]), r["mob_n"])
            _put(out, u, "truck.loading_turnaround_hours", _f(r["turn"]), r["turn_n"])
            _put(out, u, "truck.depart_after_loading_hours", _f(r["dep"]), r["dep_n"])
            if r["ontime_n"]:
                _put(out, u, "truck.on_time_delivery_pct",
                     (r["ontime_ok"] or 0) / r["ontime_n"] * 100.0, r["ontime_n"])

        for r in await rows(_Q_AUDITS):
            if r["n"]:
                _put(out, r["user_id"], "truck.safety_audit_pct",
                     (r["ok"] or 0) / r["n"] * 100.0, r["n"])

        for r in await rows(_Q_TRUCK_BDNS):
            if r["decided"]:
                _put(out, r["user_id"], "truck.bdn_first_pass_pct",
                     (r["clean"] or 0) / r["decided"] * 100.0, r["decided"])
            if r["gap_n"]:
                _put(out, r["user_id"], "truck.figure_gap_pct", _f(r["gap"]), r["gap_n"])

        for r in await rows(_Q_RUNS):
            u, runs = r["user_id"], r["runs"] or 0
            if runs:
                # Three HSE checks are required on every run.
                _put(out, u, "marine.hse_completion_pct",
                     (r["hse_done"] or 0) / (runs * 3) * 100.0, runs)
            _put(out, u, "marine.safety_incidents",
                 (r["spills"] or 0) + (r["bad_hse"] or 0), runs)

        for r in await rows(_Q_LAG):
            _put(out, r["user_id"], "marine.reporting_lag_hours", _f(r["lag"]), r["lag_n"])

        for r in await rows(_Q_CHAIN):
            _put(out, r["user_id"], "marine.documentation_pct", _f(r["pct"]), r["n"])

        for r in await rows(_Q_VESSEL_BDNS):
            u = r["user_id"]
            _put(out, u, "marine.cargo_loss_pct", _f(r["loss_pct"]), r["loss_n"])
            if r["decided"]:
                _put(out, u, "marine.bdn_first_pass_pct",
                     (r["clean"] or 0) / r["decided"] * 100.0, r["decided"])

        for r in await rows(_Q_APPROVALS):
            _put(out, r["user_id"], "ops.approval_turnaround_hours", _f(r["turnaround"]), r["n"])

        for r in await rows(_Q_OPS):
            _put(out, r["user_id"], "ops.operation_loss_pct", _f(r["loss_pct"]), r["n"])

        for r in await rows(_Q_MOBILIZATION):
            _put(out, r["user_id"], "ops.mobilization_response_hours", _f(r["hours"]), r["n"])

        for r in await rows(_Q_FINANCE):
            key = ("finance.voucher_turnaround_hours" if r["kind"] == "voucher"
                   else "finance.invoice_issuance_hours")
            _put(out, r["user_id"], key, _f(r["hours"]), r["n"])

        return out

    @staticmethod
    def score_user(role: UserRole, values: Dict,
                   overrides: Dict) -> Tuple[Optional[float], List[Dict], float]:
        """Turn one user's raw values into a score, its breakdown, and how
        much of their role the score actually covers.

        Only metrics belonging to the person's role are considered, and a
        metric with nothing recorded is left unscored rather than counted as
        zero — the same rule the operation scorecard follows.

        COVERAGE. Because unmeasured metrics drop out and the remaining
        weights renormalise, a person with one lucky measurement would
        otherwise score 100 and top the leaderboard over someone with six
        measurements and a genuine 77 — which is exactly what happened on the
        first run against real data. A score is only published once metrics
        carrying at least MIN_COVERAGE of the role's weight have something
        recorded; below that the person is reported as not yet scoreable.
        That is the honest answer, and it also removes any incentive to
        record less work in order to score better.
        """
        breakdown: List[Dict] = []
        weighted: List[Tuple[float, Optional[float]]] = []
        total_weight = 0.0
        measured_weight = 0.0

        for metric in metrics_for_role(role):
            if metric.key in NOT_PER_PERSON or metric.key not in ATTRIBUTION:
                continue
            ov = overrides.get(metric.key)
            target = ov.target if ov and ov.target is not None else metric.target
            fail = ov.fail if ov and ov.fail is not None else metric.fail
            weight = ov.weight if ov and ov.weight is not None else metric.weight

            value, sample = values.get(metric.key, (None, None))
            score = score_value(metric, value, target=target, fail=fail)
            weighted.append((weight, score))
            total_weight += weight
            if score is not None:
                measured_weight += weight
            breakdown.append({
                "key": metric.key,
                "label": metric.label,
                "unit": metric.unit,
                "value": value,
                "score": score,
                "rating": rating_for(score),
                "target": target,
                "weight": weight,
                "critical": metric.critical,
                "sample_size": sample,
                "attribution": ATTRIBUTION.get(metric.key),
                "doc": metric.doc,
                "no_data_reason": None if value is not None else "Nothing recorded for you this period",
            })

        coverage = (measured_weight / total_weight) if total_weight else 0.0
        score = weighted_score(weighted)
        if coverage < MIN_COVERAGE:
            score = None
        return score, breakdown, coverage

    @staticmethod
    async def score_everyone(db: AsyncSession, period: str) -> List[Dict]:
        """Every active staff member's score for the period, unsorted."""
        overrides = {}
        try:
            probe = (await db.execute(text(
                "SELECT to_regclass('public.kpi_targets') AS t"))).mappings().first()
            if probe and probe["t"] is not None:
                overrides = await resolve_overrides(db)
        except Exception as exc:
            logger.info("Per-user KPI: using catalog targets (%s)", exc)

        values_by_user = await UserKpiService.compute_period(db, period)

        # Human judgments live in their own table and are merged in here, so
        # a graded metric scores exactly like a measured one from this point
        # on — same curve, same weight, same rating bands.
        grades: Dict = {}
        try:
            for g in (await db.execute(_Q_GRADES, {"period": period})).mappings().all():
                grades.setdefault(g["user_id"], {})[g["metric_key"]] = {
                    "score": _f(g["score"]),
                    "reason": g["reason"],
                    "graded_by": g["graded_by_name"],
                    "graded_at": g["created_at"],
                }
        except Exception as exc:
            # kpi_grades may not be migrated yet — measured metrics still work.
            logger.info("Per-user KPI: no grades available (%s)", exc)

        users = (await db.execute(text("""
            SELECT id, full_name, email, role::text AS role
            FROM users
            WHERE role::text <> 'client' AND is_active
        """))).mappings().all()

        results: List[Dict] = []
        for u in users:
            try:
                role = UserRole(u["role"])
            except ValueError:
                continue
            values = dict(values_by_user.get(u["id"], {}))
            user_grades = grades.get(u["id"], {})
            for key, g in user_grades.items():
                if g["score"] is not None:
                    values[key] = (g["score"], 1)
            score, breakdown, coverage = UserKpiService.score_user(role, values, overrides)
            for b in breakdown:
                g = user_grades.get(b["key"])
                if g:
                    b["grade_reason"] = g["reason"]
                    b["graded_by"] = g["graded_by"]
            measured = sum(1 for b in breakdown if b["value"] is not None)
            results.append({
                "user_id": str(u["id"]),
                "full_name": u["full_name"],
                "role": u["role"],
                "role_label": role_label(role),
                "score": score,
                "rating": rating_for(score),
                "metrics_measured": measured,
                "metrics_total": len(breakdown),
                "coverage": round(coverage * 100, 1),
                "insufficient_data": score is None and measured > 0,
                "breakdown": breakdown,
            })
        return results


def team_median(scores: List[Optional[float]]) -> Optional[float]:
    """Median of the scores that exist.

    Median rather than mean on purpose: it is what a person is compared
    against on their own page, and one disastrous month for one colleague
    should not move everybody else's benchmark.
    """
    present = sorted(s for s in scores if s is not None)
    if not present:
        return None
    mid = len(present) // 2
    if len(present) % 2:
        return present[mid]
    return (present[mid - 1] + present[mid]) / 2
