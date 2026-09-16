"""Score history — is this getting better or worse?

A single month's score answers "how are we doing". It cannot answer "are we
improving", which is the question that actually changes behaviour. That needs
history, and history needs months that have stopped moving.

**Closed months are the truth here.** `kpi_snapshots` holds them, frozen, so a
correction to an old record cannot silently restate what somebody was graded on
six months ago — the same reasoning that makes ROB entries append-only.

The current month is different: still running, still changing. It is returned
too, because a trend ending three weeks ago is not much use, but it is flagged
`provisional` so no screen can present it as settled.

**Nothing here writes.** Freezing a month is `POST /kpi/snapshots/close`, which
is a management act and stays a separate, deliberate decision.
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.catalog import rating_for
from app.kpi.user_service import UserKpiService, current_period, team_median

logger = logging.getLogger("raoms")


def _previous_periods(count: int) -> List[str]:
    """The last `count` periods, oldest first, ending with the current month."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    out: List[str] = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(out))


class TrendService:

    @staticmethod
    async def _closed_periods(db: AsyncSession, periods: List[str]) -> Dict[str, List[Dict]]:
        """Frozen scores, grouped by period. Empty until a month is closed."""
        if not periods:
            return {}
        try:
            rows = (await db.execute(text("""
                SELECT period, subject_id, subject_role, score, rating
                FROM kpi_snapshots
                WHERE subject_type = 'user' AND period = ANY(:periods)
            """), {"periods": periods})).mappings().all()
        except Exception as exc:
            # The table may not be migrated on every environment.
            logger.info("Trend history unavailable (%s)", exc)
            return {}

        grouped: Dict[str, List[Dict]] = {}
        for r in rows:
            grouped.setdefault(r["period"], []).append({
                "user_id": str(r["subject_id"]) if r["subject_id"] else None,
                "role": r["subject_role"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "rating": r["rating"],
            })
        return grouped

    @staticmethod
    async def company_trend(db: AsyncSession, months: int = 6) -> Dict:
        """Company-wide median score per month, plus per-role series."""
        periods = _previous_periods(months)
        current = current_period()
        closed = await TrendService._closed_periods(db, periods)

        points: List[Dict] = []
        role_series: Dict[str, List[Dict]] = {}

        for period in periods:
            provisional = period == current
            if provisional:
                # Compute the live month rather than showing a gap at the end.
                rows = await UserKpiService.score_everyone(db, period)
                entries = [
                    {"user_id": r["user_id"], "role": r["role"],
                     "score": r["score"], "rating": r["rating"]}
                    for r in rows
                ]
            else:
                entries = closed.get(period, [])

            scores = [e["score"] for e in entries if e["score"] is not None]
            median = team_median(scores)
            points.append({
                "period": period,
                "score": median,
                "rating": rating_for(median),
                "people_scored": len(scores),
                "provisional": provisional,
                # A closed month with no rows was never frozen, which is a
                # different fact from "everybody scored nothing".
                "recorded": provisional or period in closed,
            })

            by_role: Dict[str, List[float]] = {}
            for e in entries:
                if e["score"] is not None and e["role"]:
                    by_role.setdefault(e["role"], []).append(e["score"])
            for role, vals in by_role.items():
                role_series.setdefault(role, []).append({
                    "period": period,
                    "score": team_median(vals),
                    "provisional": provisional,
                })

        closed_count = sum(1 for p in points if p["recorded"] and not p["provisional"])
        return {
            "points": points,
            "roles": role_series,
            "closed_months": closed_count,
            # Two points are the minimum for a direction to exist at all.
            "has_history": closed_count >= 1,
            "message": (
                None if closed_count
                else "No month has been closed yet, so there is no history to "
                     "compare against. The current month is shown on its own "
                     "and is still changing."
            ),
        }

    @staticmethod
    async def user_trend(db: AsyncSession, user_id: str, months: int = 6) -> Dict:
        """One person's own history. Never accepts another user's id from a
        request — the route passes the authenticated user's own."""
        periods = _previous_periods(months)
        current = current_period()
        closed = await TrendService._closed_periods(db, periods)

        points: List[Dict] = []
        for period in periods:
            provisional = period == current
            score = rating = None
            recorded = False

            if provisional:
                rows = await UserKpiService.score_everyone(db, period)
                mine = next((r for r in rows if r["user_id"] == user_id), None)
                if mine:
                    score, rating, recorded = mine["score"], mine["rating"], True
            else:
                entry = next(
                    (e for e in closed.get(period, []) if e["user_id"] == user_id),
                    None,
                )
                if entry:
                    score, rating, recorded = entry["score"], entry["rating"], True

            points.append({
                "period": period,
                "score": score,
                "rating": rating,
                "provisional": provisional,
                "recorded": recorded,
            })

        real = [p["score"] for p in points if p["score"] is not None]
        direction: Optional[str] = None
        if len(real) >= 2:
            change = real[-1] - real[0]
            direction = "improving" if change > 2 else "slipping" if change < -2 else "steady"

        return {
            "points": points,
            "direction": direction,
            "has_history": len(real) >= 2,
            "message": (
                None if len(real) >= 2
                else "Not enough closed months yet to show a trend."
            ),
        }
