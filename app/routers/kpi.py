from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.services.kpi_service import KpiService

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.get("/operations/{operation_id}/kpi", response_model=StandardResponse)
async def get_operation_kpi(
    operation_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Cast-off (earliest across all vessel runs) to final discharge-completed
    (latest) — the overall operation duration, plus a per-vessel-run breakdown."""
    kpi = await KpiService.get_operation_kpi(operation_id, db)
    return StandardResponse.ok(data=kpi.model_dump())


@router.get("/operations/{operation_id}/kpi/stage-durations", response_model=StandardResponse)
async def get_operation_stage_durations(
    operation_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Per-stage timing breakdown per vessel run, with the actual user/role
    who logged each stage advance (from the audit trail) — not a guess."""
    durations = await KpiService.get_role_stage_durations(operation_id, db)
    return StandardResponse.ok(data=durations.model_dump())
