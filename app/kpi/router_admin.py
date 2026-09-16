"""Grading, monthly reports, HR pack and the month-close job (Phase 4).

Access follows the boundary already drawn in app/permissions.py:

* **Grading** is a judgment about a person, so it is Bunker-Manager-only —
  the same reasoning that keeps the Ops Supervisor out of user admin.
* **Your own report** is available to any staff member, for themselves only,
  with no user parameter — the same structural rule as `/kpi/me`.
* **The HR pack and the month-close job** are company-wide, so BM only.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.kpi.catalog import CATALOG, MetricSource
from app.kpi.reports import ReportService
from app.kpi.snapshots import SnapshotService
from app.kpi.user_service import current_period
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))

_PERIOD = Query(
    None,
    pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    description="Calendar month as YYYY-MM. Defaults to the current month.",
)


def _period_or_now(period: Optional[str]) -> str:
    chosen = period or current_period()
    if chosen > current_period():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That month has not happened yet")
    return chosen


class GradeIn(BaseModel):
    user_id: UUID
    metric_key: str
    score: float = Field(ge=0, le=100)
    period: Optional[str] = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    # The same reason-gated convention as every other judgment in this system.
    # Ten characters is the floor used for BDN rejections; a grade deserves at
    # least as much, because the person being graded will read it.
    reason: str = Field(min_length=10)


@router.post("/kpi/grades", response_model=StandardResponse)
async def set_grade(
    payload: GradeIn,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Record a judgment about someone's work, with the reason attached.

    Append-only: a revised grade is a new row and the latest one in the period
    wins. The earlier judgment stays readable, so what someone was told, and
    when, survives a change of mind.
    """
    metric = CATALOG.get(payload.metric_key)
    if not metric:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Unknown metric '{payload.metric_key}'")
    if metric.source is not MetricSource.graded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"'{metric.label}' is measured from records, not graded. "
                    "Only judgment-based measures can be graded by hand."),
        )

    period = _period_or_now(payload.period)

    target = (await db.execute(text(
        "SELECT id, role::text AS role FROM users WHERE id = :uid AND is_active"
    ), {"uid": str(payload.user_id)})).mappings().first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such active user")
    if target["role"] != metric.role.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{metric.label}' does not apply to that person's role",
        )

    # Mark any previous grade for the same person/metric/period as superseded
    # rather than deleting it — the history is the point.
    await db.execute(text("""
        UPDATE kpi_grades SET superseded_at = now()
        WHERE user_id = :uid AND metric_key = :mk AND period = :p
          AND superseded_at IS NULL
    """), {"uid": str(payload.user_id), "mk": payload.metric_key, "p": period})

    await db.execute(text("""
        INSERT INTO kpi_grades (user_id, period, metric_key, score, reason, graded_by)
        VALUES (:uid, :p, :mk, :score, :reason, :by)
    """), {
        "uid": str(payload.user_id), "p": period, "mk": payload.metric_key,
        "score": payload.score, "reason": payload.reason.strip(),
        "by": str(current_user.id),
    })

    return StandardResponse.ok(
        data={"metric_key": payload.metric_key, "period": period, "score": payload.score},
        message=f"Recorded for {metric.label}",
    )


@router.get("/kpi/grades/metrics", response_model=StandardResponse)
async def list_gradable_metrics(current_user: User = _bm_only):
    """Which measures are judged rather than counted, and for which role."""
    from app.models.enums import role_label
    items = [
        {
            "metric_key": m.key,
            "label": m.label,
            "role": m.role.value,
            "role_label": role_label(m.role),
            "weight": m.weight,
            "doc": m.doc,
            "description": m.description,
        }
        for m in CATALOG.values() if m.source is MetricSource.graded
    ]
    items.sort(key=lambda i: (i["role_label"], i["label"]))
    return StandardResponse.ok(data=items)


@router.get("/kpi/reports/me", response_model=StandardResponse)
async def my_monthly_report(
    period: Optional[str] = _PERIOD,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your monthly report, pre-filled from the records.

    Takes no user parameter, for the same reason `/kpi/me` does not: there is
    no version of this request that returns somebody else's report.
    """
    if current_user.role == UserRole.client:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    report = await ReportService.build(db, str(current_user.id), _period_or_now(period))
    return StandardResponse.ok(data=report)


@router.get("/kpi/reports/hr-pack", response_model=StandardResponse)
async def hr_pack(
    period: Optional[str] = _PERIOD,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Every staff member's figures and evidence for one month, so grading is
    done from the record rather than from memory."""
    pack = await ReportService.hr_pack(db, _period_or_now(period))
    return StandardResponse.ok(data=pack)


@router.post("/kpi/snapshots/close", response_model=StandardResponse)
async def close_period(
    period: Optional[str] = _PERIOD,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Write this month's scores into `kpi_snapshots`.

    A month that has already ended is frozen, and a frozen row is never
    rewritten — so re-running this is safe and cannot restate a score somebody
    has already been shown. The current month is rewritten each run, which is
    what keeps the Command Center's team pulse current.
    """
    result = await SnapshotService.write_period(db, _period_or_now(period))
    return StandardResponse.ok(
        data=result,
        message=(f"{result['subjects_written']} scores written for {result['period']}"
                 + (f"; {result['protected_frozen_rows']} frozen rows left untouched"
                    if result["protected_frozen_rows"] else "")),
    )
