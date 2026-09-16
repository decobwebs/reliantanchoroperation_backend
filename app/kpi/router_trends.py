"""Trend endpoints.

`/kpi/trends/company` is Bunker-Manager-only: it aggregates everybody.

`/kpi/trends/me` returns the caller's own history and takes the user id from
the authenticated session, never from the request — the same rule that governs
`/kpi/me`, so one person's history cannot be requested by another.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.kpi.trends import TrendService
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.get("/kpi/trends/company", response_model=StandardResponse)
async def company_trend(
    months: int = Query(6, ge=2, le=24),
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Company median score per month, with a series per role."""
    return StandardResponse.ok(data=await TrendService.company_trend(db, months))


@router.get("/kpi/trends/me", response_model=StandardResponse)
async def my_trend(
    months: int = Query(6, ge=2, le=24),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own score history. No user parameter exists, by design."""
    return StandardResponse.ok(
        data=await TrendService.user_trend(db, str(current_user.id), months)
    )
