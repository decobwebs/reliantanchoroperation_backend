"""Per-person KPI endpoints (Phase 2).

THE VISIBILITY RULE, and how it is enforced.

The Bunker Manager decided: he sees the full named leaderboard; every other
staff member sees only their own score against an anonymous team median. No
staff member ever sees a colleague's number.

`/kpi/me` therefore takes **no user parameter at all**. It is not that a
user_id would be rejected — there is nowhere to put one. The identity comes
from the bearer token and nothing else, so there is no version of this request
that returns somebody else's scorecard, however it is crafted. Please keep it
that way: adding a "just for admins" user_id here is exactly how this kind of
rule quietly dies.

The leaderboard is a separate, Bunker-Manager-only route. It is company-wide
and sits outside the operation boundary, so the Ops Supervisor does not get
it — the same reasoning that keeps them out of user admin and finance.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.kpi.schemas_me import (
    Leaderboard, LeaderboardRow, MyMetric, MyScorecard,
)
from app.kpi.user_service import UserKpiService, current_period, team_median
from app.models.enums import UserRole, role_label
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))

_PERIOD = Query(
    None,
    pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    description="Calendar month as YYYY-MM. Defaults to the current month.",
)


def _resolve_period(period: Optional[str]) -> tuple[str, bool]:
    """Returns (period, provisional). A month still running is provisional —
    its figures keep moving, and saying so stops anyone treating a mid-month
    number as final."""
    now_period = current_period()
    chosen = period or now_period
    if chosen > now_period:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That month has not happened yet",
        )
    return chosen, chosen == now_period


@router.get("/kpi/me", response_model=StandardResponse)
async def get_my_scorecard(
    period: Optional[str] = _PERIOD,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own performance, and only ever your own.

    Every metric names the records it came from and the job document its
    target came from, so a figure you disagree with can be checked rather
    than argued about.
    """
    if current_user.role == UserRole.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    chosen, provisional = _resolve_period(period)
    everyone = await UserKpiService.score_everyone(db, chosen)

    mine = next((r for r in everyone if r["user_id"] == str(current_user.id)), None)
    if mine is None:
        # A real account with nothing recorded this month — an empty
        # scorecard, not an error.
        role = current_user.role
        mine = {
            "user_id": str(current_user.id),
            "full_name": current_user.full_name,
            "role": role.value,
            "role_label": role_label(role),
            "score": None, "rating": None,
            "metrics_measured": 0, "metrics_total": 0,
            "coverage": 0.0, "insufficient_data": False, "breakdown": [],
        }

    # Compared against their own role only — a Truck Operation officer's score
    # means nothing next to a Finance Manager's, since the two are built from
    # entirely different metrics.
    peers = [r for r in everyone if r["role"] == mine["role"]]

    card = MyScorecard(
        period=chosen,
        user_id=mine["user_id"],
        full_name=mine["full_name"],
        role=mine["role"],
        role_label=mine["role_label"],
        score=mine["score"],
        rating=mine["rating"],
        metrics_measured=mine["metrics_measured"],
        metrics_total=mine["metrics_total"],
        coverage=mine["coverage"],
        insufficient_data=mine["insufficient_data"],
        metrics=[MyMetric(**m) for m in mine["breakdown"]],
        team_median=team_median([p["score"] for p in peers]),
        team_size=sum(1 for p in peers if p["score"] is not None),
        provisional=provisional,
    )
    return StandardResponse.ok(data=card.model_dump(mode="json"))


@router.get("/kpi/leaderboard", response_model=StandardResponse)
async def get_leaderboard(
    period: Optional[str] = _PERIOD,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Every staff member's score for the month, named. Bunker Manager only.

    People with nothing recorded are listed with a blank score rather than
    dropped: "no work recorded this month" is itself worth seeing, and
    silently omitting them would make the list quietly misleading.
    """
    chosen, provisional = _resolve_period(period)
    everyone = await UserKpiService.score_everyone(db, chosen)

    rows = [LeaderboardRow(**{k: r[k] for k in (
        "user_id", "full_name", "role", "role_label", "score", "rating",
        "metrics_measured", "metrics_total", "coverage", "insufficient_data",
    )}) for r in everyone]

    # Scored people first, best to worst; the unscored fall to the bottom in
    # role order rather than being sprinkled through the ranking.
    rows.sort(key=lambda r: (r.score is None, -(r.score or 0), r.role_label, r.full_name or ""))

    board = Leaderboard(
        period=chosen,
        provisional=provisional,
        rows=rows,
        team_median=team_median([r.score for r in rows]),
        scored_count=sum(1 for r in rows if r.score is not None),
        unscored_count=sum(1 for r in rows if r.score is None),
    )
    return StandardResponse.ok(data=board.model_dump(mode="json"))
