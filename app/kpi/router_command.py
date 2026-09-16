"""Command Center endpoint. Phase 3 owns this file.

Bunker-Manager-only. The Command Center spans the whole company — money,
fleet, licences, every operation and every person's scores — which puts it
firmly outside the operation boundary the Ops Supervisor works within (see
app/permissions.py). It is deliberately the same rule that keeps the Ops
Supervisor out of user admin, licences and finance.

One endpoint returns the whole dashboard. See the latency note in
command_service.py for why that is not negotiable.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.kpi.command_service import CommandCenterService
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.get("/kpi/command-center", response_model=StandardResponse)
async def get_command_center(
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Everything the Bunker Manager needs, in one response.

    Exception-first: `attention` leads, sorted by severity then age. Any panel
    that could not be computed is named in `degraded` so the interface can say
    so rather than rendering an empty card that looks like good news.
    """
    data = await CommandCenterService.build(db)
    return StandardResponse.ok(data=data.model_dump(mode="json"))
