"""Writing monthly scores into `kpi_snapshots` (finishes Phase 2, feeds Phase 4).

Phase 1 and 2 compute scores on read, which is right while a month is still
running — a corrected figure should show up immediately. But a month that has
been closed, discussed with someone and used in a review must stop moving. If
a truck record is corrected in October, nobody's August score should silently
change underneath a conversation that already happened.

So each closed month is written down once and marked `frozen`, and a frozen
row is never rewritten. That is the same convention ROB entries, vessel ETAs
and sent-email logs already follow in this system: evidence is appended and
corrected alongside, never overwritten in place.

The current month is written too, unfrozen, so the Command Center's team
pulse has something to read — it is simply recomputed on every run.
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.user_service import UserKpiService, current_period

logger = logging.getLogger("raoms")


# `WHERE kpi_snapshots.frozen = false` is the whole safety property: a closed
# month is written once and is thereafter immutable, even if this job is run
# again by accident.
_UPSERT = text("""
    INSERT INTO kpi_snapshots
        (period, subject_type, subject_id, subject_role, score, rating,
         metrics, frozen, computed_at)
    VALUES
        (:period, 'user', :subject_id, :subject_role, :score, :rating,
         CAST(:metrics AS jsonb), :frozen, now())
    ON CONFLICT (period, subject_type, subject_id) DO UPDATE
       SET score        = EXCLUDED.score,
           rating       = EXCLUDED.rating,
           subject_role = EXCLUDED.subject_role,
           metrics      = EXCLUDED.metrics,
           frozen       = EXCLUDED.frozen,
           computed_at  = now()
     WHERE kpi_snapshots.frozen = false
""")


def _compact(breakdown: List[Dict]) -> List[Dict]:
    """Keep enough per metric to explain a score months later, without
    storing the whole catalog again."""
    return [
        {
            "key": b["key"],
            "value": b["value"],
            "score": b["score"],
            "target": b["target"],
            "weight": b["weight"],
            "n": b["sample_size"],
        }
        for b in breakdown
    ]


class SnapshotService:

    @staticmethod
    async def write_period(db: AsyncSession, period: str,
                           freeze: Optional[bool] = None) -> Dict:
        """Compute and store every staff member's score for one month.

        `freeze` defaults to "true for any month that has already ended".
        Pass it explicitly only to override that, and note that freezing is
        one-way — a frozen row is never updated again by this job.
        """
        import json

        if freeze is None:
            freeze = period < current_period()

        rows = await UserKpiService.score_everyone(db, period)

        written = 0
        skipped_frozen = 0
        for r in rows:
            result = await db.execute(_UPSERT, {
                "period": period,
                "subject_id": r["user_id"],
                "subject_role": r["role"],
                "score": r["score"],
                "rating": r["rating"],
                "metrics": json.dumps(_compact(r["breakdown"])),
                "frozen": freeze,
            })
            # rowcount 0 means the ON CONFLICT ... WHERE guard refused to
            # touch an already-frozen row. That is a success, not a failure.
            if result.rowcount:
                written += 1
            else:
                skipped_frozen += 1

        logger.info(
            "KPI snapshots: period=%s written=%d protected=%d frozen=%s",
            period, written, skipped_frozen, freeze,
        )
        return {
            "period": period,
            "frozen": freeze,
            "subjects_written": written,
            "protected_frozen_rows": skipped_frozen,
            "scored": sum(1 for r in rows if r["score"] is not None),
            "total_staff": len(rows),
        }
