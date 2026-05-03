from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# Accessible to BM, Finance Manager, and Ops Supervisor
_analytics_roles = Depends(
    require_roles(UserRole.bunker_manager, UserRole.finance_manager, UserRole.ops_supervisor)
)


@router.get("/dashboard", response_model=StandardResponse)
async def get_dashboard(
    current_user: User = _analytics_roles,
    db: AsyncSession = Depends(get_db),
):
    """Full analytics dashboard: operations summary, truck stats, vessel ROB, revenue."""
    dashboard = await AnalyticsService.get_dashboard(db)
    return StandardResponse.ok(data=dashboard.model_dump(), message="Dashboard data retrieved")


@router.get("/operations/monthly", response_model=StandardResponse)
async def get_monthly_operations(
    year: int = Query(default=None),
    current_user: User = _analytics_roles,
    db: AsyncSession = Depends(get_db),
):
    """Monthly operation counts for a given year (defaults to current year)."""
    target_year = year or datetime.utcnow().year
    data = await AnalyticsService.get_operations_summary(db, target_year)
    return StandardResponse.ok(
        data={"year": target_year, "months": data},
        message="Monthly operations retrieved",
    )
