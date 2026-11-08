"""People pages for HR and the Bunker Manager (HR improvements, Nov 2026).

Management agreed on 1 Nov 2026 that HR sees every staff member's named
score, read only. Until now HR could see a single number per person and
nothing behind it. These routes open the same detail a person sees about
themselves on /my-performance, but only to PERFORMANCE_VIEWER_ROLES:

* GET /kpi/people                    staff directory (no scores)
* GET /kpi/people/{id}               one person's scorecard and breakdown
* GET /kpi/people/{id}/trend         one person's score history
* GET /kpi/people/{id}/report        one person's monthly report

Why a separate router instead of a user_id on /kpi/me: /kpi/me is built so
that no staff member can ever ask for a colleague's number (see router_me).
That rule stays. These routes refuse everyone outside BM and HR before they
read anything, and every one of them only reads.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.kpi.reports import ReportService
from app.kpi.router_me import _PERIOD, _resolve_period
from app.kpi.schemas_me import MyMetric, MyScorecard
from app.kpi.trends import TrendService
from app.kpi.user_service import UserKpiService, team_median
from app.models.enums import UserRole, role_label
from app.models.user import User
from app.permissions import PERFORMANCE_VIEWER_ROLES
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_viewers = Depends(require_roles(*PERFORMANCE_VIEWER_ROLES))


async def _staff_member(db: AsyncSession, user_id: UUID) -> User:
    """The person being looked at. Clients are not staff and have no scorecard."""
    person = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if person is None or person.role == UserRole.client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    return person


@router.get("/kpi/people", response_model=StandardResponse)
async def staff_directory(
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    """Every staff account, active and switched off. Read only.

    Contact details, role, when they joined and when they last logged in.
    Account changes (create, switch off, reset) stay on /admin, BM only.
    """
    rows = (await db.execute(
        select(User).where(User.role != UserRole.client).order_by(User.full_name)
    )).scalars().all()
    return StandardResponse.ok(data=[
        {
            "user_id": str(u.id),
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "role": u.role.value,
            "role_label": role_label(u.role),
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        }
        for u in rows
    ])


@router.get("/kpi/people/{user_id}", response_model=StandardResponse)
async def person_scorecard(
    user_id: UUID,
    period: Optional[str] = _PERIOD,
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    """One person's scorecard, in the same shape as /kpi/me."""
    person = await _staff_member(db, user_id)
    chosen, provisional = _resolve_period(period)
    everyone = await UserKpiService.score_everyone(db, chosen)

    row = next((r for r in everyone if r["user_id"] == str(person.id)), None)
    if row is None:
        # Switched off, or a role that is not scored (HR, Safety Officer):
        # an empty card, not an error.
        row = {
            "user_id": str(person.id),
            "full_name": person.full_name,
            "role": person.role.value,
            "role_label": role_label(person.role),
            "score": None, "rating": None,
            "metrics_measured": 0, "metrics_total": 0,
            "coverage": 0.0, "insufficient_data": False, "breakdown": [],
        }

    # Same rule as /kpi/me: compared with their own role only.
    peers = [r for r in everyone if r["role"] == row["role"]]

    card = MyScorecard(
        period=chosen,
        user_id=row["user_id"],
        full_name=row["full_name"],
        role=row["role"],
        role_label=row["role_label"],
        score=row["score"],
        rating=row["rating"],
        metrics_measured=row["metrics_measured"],
        metrics_total=row["metrics_total"],
        coverage=row["coverage"],
        insufficient_data=row["insufficient_data"],
        metrics=[MyMetric(**m) for m in row["breakdown"]],
        team_median=team_median([p["score"] for p in peers]),
        team_size=sum(1 for p in peers if p["score"] is not None),
        provisional=provisional,
    )
    data = card.model_dump(mode="json")
    data["email"] = person.email
    data["phone"] = person.phone
    data["is_active"] = person.is_active
    # False for roles the KPI engine does not score at all (HR, Safety
    # Officer), so the page can say "not scored" instead of "no work".
    data["scored_role"] = bool(peers)
    return StandardResponse.ok(data=data)


@router.get("/kpi/people/{user_id}/trend", response_model=StandardResponse)
async def person_trend(
    user_id: UUID,
    months: int = Query(6, ge=2, le=24),
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    """One person's score history, month by month."""
    person = await _staff_member(db, user_id)
    return StandardResponse.ok(data=await TrendService.user_trend(db, str(person.id), months))


@router.get("/kpi/people/{user_id}/report", response_model=StandardResponse)
async def person_report(
    user_id: UUID,
    period: Optional[str] = _PERIOD,
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    """One person's monthly report, filled in from the records."""
    person = await _staff_member(db, user_id)
    chosen, _ = _resolve_period(period)
    return StandardResponse.ok(data=await ReportService.build(db, str(person.id), chosen))
