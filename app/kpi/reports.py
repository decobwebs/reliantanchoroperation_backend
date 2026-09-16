"""The report that writes itself (Phase 4).

Every one of the eleven job-responsibility documents ends with a report
template the person is expected to fill in each month by hand — total trucks
loaded, quantities, variances, turnaround times, incidents. RAOMS already
holds almost all of those numbers.

So the system fills them in. What is left blank is only what a person
genuinely has to write: challenges, recommendations, next period's plan.

Two things this buys beyond the saved hours. Reported numbers now always
match the database, so nobody can accidentally (or deliberately) report a
prettier variance than the records hold. And because the sections mirror the
company's own templates, the output is the document management already reads
— not a new format anyone has to learn.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.catalog import CATALOG
from app.kpi.user_service import UserKpiService, period_bounds
from app.models.enums import UserRole


def _fmt(value: Optional[float], unit: str = "", dp: int = 0) -> str:
    """Blank for absent, always — the same rule the UI follows. A report that
    prints 0 where nothing was measured is a report that lies quietly."""
    if value is None:
        return ""
    text_value = f"{value:,.{dp}f}"
    return f"{text_value} {unit}".strip() if unit else text_value


# Per-role figure blocks, keyed by the section headings the company's own
# templates use. Only what the system can answer honestly.
# PAIRED SUMS. Loaded and discharged totals count only trucks carrying BOTH
# figures. Summing every loaded figure against only those that also have a
# discharged one compares a whole fleet against part of it — on the first run
# it reported a 23% variance for someone whose real average loss was 2,098 L
# per truck. A report going to management must not carry that.
_Q_TRUCK_FIGURES = text("""
    SELECT count(*)                                              AS trucks,
           count(DISTINCT operation_id)                          AS operations,
           count(*) FILTER (WHERE quantity_loaded_mt IS NOT NULL
                              AND quantity_discharged_mt IS NOT NULL) AS reconciled,
           sum(quantity_loaded_mt)
             FILTER (WHERE quantity_loaded_mt IS NOT NULL
                       AND quantity_discharged_mt IS NOT NULL)   AS loaded,
           sum(quantity_discharged_mt)
             FILTER (WHERE quantity_loaded_mt IS NOT NULL
                       AND quantity_discharged_mt IS NOT NULL)   AS discharged,
           avg(abs(quantity_loaded_mt - quantity_discharged_mt))
             FILTER (WHERE quantity_loaded_mt IS NOT NULL
                       AND quantity_discharged_mt IS NOT NULL)   AS avg_loss,
           count(*) FILTER (WHERE quantity_loaded_mt IS NOT NULL
                              AND quantity_discharged_mt IS NOT NULL
                              AND abs(quantity_loaded_mt - quantity_discharged_mt) > :cap)
                                                                 AS over_cap,
           avg(EXTRACT(EPOCH FROM (discharge_end_at - discharge_start_at))/3600.0)
             FILTER (WHERE discharge_end_at >= discharge_start_at) AS avg_discharge_h
    FROM truck_operations
    WHERE logged_by = :uid AND created_at >= :s AND created_at < :e
""")

_Q_MARINE_FIGURES = text("""
    SELECT count(*) AS runs,
           count(DISTINCT operation_id) AS operations,
           count(*) FILTER (WHERE coalesce(spillage_mt, 0) > 0) AS spills,
           sum( (CASE WHEN hse_conducted_at        IS NOT NULL THEN 1 ELSE 0 END)
              + (CASE WHEN hse_during_conducted_at IS NOT NULL THEN 1 ELSE 0 END)
              + (CASE WHEN hse_post_conducted_at   IS NOT NULL THEN 1 ELSE 0 END) ) AS hse_done
    FROM vessel_activities
    WHERE assigned_to = :uid AND status::text <> 'cancelled'
      AND created_at >= :s AND created_at < :e
""")

_Q_OPS_FIGURES = text("""
    SELECT count(*) AS operations,
           count(*) FILTER (WHERE status::text = 'completed') AS completed
    FROM operations
    WHERE created_by = :uid AND deleted_at IS NULL
      AND created_at >= :s AND created_at < :e
""")

_Q_APPROVALS_MADE = text("""
    SELECT count(*) AS decided FROM (
        SELECT id FROM bdns
         WHERE reviewed_by = :uid AND approved_at >= :s AND approved_at < :e
        UNION ALL
        SELECT id FROM truck_bdns
         WHERE reviewed_by = :uid AND approved_at >= :s AND approved_at < :e
        UNION ALL
        SELECT id FROM truck_feedback
         WHERE reviewed_by = :uid AND reviewed_at >= :s AND reviewed_at < :e
    ) x
""")

# The narrative sections the system must NOT invent. Every job document asks
# for these and only the person can answer them.
_NARRATIVE_SECTIONS = [
    "Challenges and issues encountered",
    "Recommendations",
    "Plan for next period",
]


class ReportService:

    @staticmethod
    async def build(db: AsyncSession, user_id: str, period: str) -> Dict:
        """One person's monthly report, pre-filled from the record."""
        start, end = period_bounds(period)
        p = {"uid": user_id, "s": start, "e": end}

        user = (await db.execute(text(
            "SELECT id, full_name, email, role::text AS role FROM users WHERE id = :uid"
        ), {"uid": user_id})).mappings().first()
        if not user:
            raise ValueError("No such user")

        role = UserRole(user["role"])

        # The score and its breakdown come from the same engine everything
        # else uses — the report can never disagree with the scorecard.
        everyone = await UserKpiService.score_everyone(db, period)
        mine = next((r for r in everyone if r["user_id"] == str(user_id)), None)

        sections: List[Dict] = []

        if role in (UserRole.logistics_officer,):
            # The loss cap comes from the catalog (and any BM override), never
            # a literal — the whole point of kpi_targets is that it moves.
            cap = CATALOG["truck.loss_litres_per_truck"].target
            r = (await db.execute(_Q_TRUCK_FIGURES, {**p, "cap": cap})).mappings().first()
            loaded, discharged = r["loaded"], r["discharged"]
            variance = (float(loaded) - float(discharged)) if loaded and discharged else None
            pct = (abs(variance) / float(loaded) * 100.0) if variance is not None and loaded else None
            reconciled, trucks = r["reconciled"] or 0, r["trucks"] or 0
            sections.append({
                "heading": "Truck operations summary",
                "source": "Filled in from truck records you logged",
                "rows": [
                    ("Trucks handled", _fmt(trucks)),
                    ("Operations involved", _fmt(r["operations"])),
                    # Says plainly how much of the month the totals cover, so
                    # nobody reads a partial reconciliation as the whole month.
                    ("Trucks with both loaded and discharged figures",
                     f"{reconciled} of {trucks}" if trucks else ""),
                    ("Quantity loaded (reconciled trucks)", _fmt(float(loaded) if loaded else None, "L")),
                    ("Quantity discharged (reconciled trucks)", _fmt(float(discharged) if discharged else None, "L")),
                    ("Variance", _fmt(variance, "L")),
                    ("Variance as a percentage", _fmt(pct, "%", 2)),
                    ("Average loss per truck", _fmt(float(r["avg_loss"]) if r["avg_loss"] else None, "L")),
                    (f"Trucks exceeding the {cap:,.0f} L limit", _fmt(r["over_cap"])),
                    ("Average discharge time per truck", _fmt(float(r["avg_discharge_h"]) if r["avg_discharge_h"] else None, "hours", 1)),
                ],
            })

        if role in (UserRole.cargo_superintendent,):
            r = (await db.execute(_Q_MARINE_FIGURES, p)).mappings().first()
            runs = r["runs"] or 0
            sections.append({
                "heading": "Vessel operations summary",
                "source": "Filled in from vessel runs assigned to you",
                "rows": [
                    ("Vessel runs handled", _fmt(runs)),
                    ("Operations involved", _fmt(r["operations"])),
                    ("HSE checks recorded", f"{r['hse_done'] or 0} of {runs * 3}" if runs else ""),
                    ("Spill incidents", _fmt(r["spills"])),
                ],
            })

        if role in (UserRole.bunker_manager, UserRole.ops_supervisor):
            r = (await db.execute(_Q_OPS_FIGURES, p)).mappings().first()
            a = (await db.execute(_Q_APPROVALS_MADE, p)).mappings().first()
            sections.append({
                "heading": "Operations summary",
                "source": "Filled in from operations you created and items you approved",
                "rows": [
                    ("Operations created", _fmt(r["operations"])),
                    ("Operations completed", _fmt(r["completed"])),
                    ("Documents approved or rejected", _fmt(a["decided"])),
                ],
            })

        # Performance section — identical figures to the person's own page.
        if mine:
            perf_rows = []
            for m in mine["breakdown"]:
                if m["value"] is None:
                    continue
                dp = 2 if m["unit"] == "%" else 1 if m["unit"] == "hours" else 0
                perf_rows.append((
                    m["label"],
                    f"{_fmt(m['value'], '' if m['unit'] in ('count', 'score') else m['unit'], dp)}"
                    f"  (target {_fmt(m['target'], '', dp)}, scored {_fmt(m['score'], '', 0)})",
                ))
            sections.append({
                "heading": "Performance against targets",
                "source": "Every target quoted from your job responsibilities document",
                "rows": perf_rows or [("No measures recorded this period", "")],
            })

        return {
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user["id"]),
            "full_name": user["full_name"],
            "role": user["role"],
            "score": mine["score"] if mine else None,
            "rating": mine["rating"] if mine else None,
            "coverage": mine["coverage"] if mine else 0.0,
            "sections": sections,
            # Left deliberately empty: the system must not invent a person's
            # own account of what went wrong or what they intend to do next.
            "to_complete": _NARRATIVE_SECTIONS,
        }

    @staticmethod
    async def hr_pack(db: AsyncSession, period: str) -> Dict:
        """One month, every staff member — what HR needs to grade from
        evidence instead of memory.

        The Operations Manager document says KPIs are "monitored and graded by
        HR after each operation / monthly review". This is that input.
        """
        everyone = await UserKpiService.score_everyone(db, period)
        everyone.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))

        return {
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "staff_total": len(everyone),
            "scored": sum(1 for r in everyone if r["score"] is not None),
            "people": [
                {
                    "user_id": r["user_id"],
                    "full_name": r["full_name"],
                    "role_label": r["role_label"],
                    "score": r["score"],
                    "rating": r["rating"],
                    "coverage": r["coverage"],
                    "metrics_measured": r["metrics_measured"],
                    "metrics_total": r["metrics_total"],
                    "metrics": [
                        {
                            "label": m["label"],
                            "value": m["value"],
                            "unit": m["unit"],
                            "target": m["target"],
                            "score": m["score"],
                            "critical": m["critical"],
                            "attribution": m["attribution"],
                            "doc": m["doc"],
                            "grade_reason": m.get("grade_reason"),
                            "graded_by": m.get("graded_by"),
                        }
                        for m in r["breakdown"]
                    ],
                }
                for r in everyone
            ],
        }
