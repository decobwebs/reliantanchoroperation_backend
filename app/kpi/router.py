"""KPI endpoints.

Access follows the boundary already drawn in app/permissions.py, with no new
concepts invented for this module:

* A **scorecard** is operation-scoped, so anyone who can already open the
  operation can see it. Showing someone a score computed from work they can
  read anyway leaks nothing, and hiding it would make the numbers feel like
  something being done to people rather than with them.
* **Targets** are a company-wide policy setting, which puts them outside
  operations and therefore Bunker-Manager-only — the same reasoning that
  keeps the Ops Supervisor out of user admin, licences and finance.
"""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.kpi.catalog import CATALOG, MetricDef
from app.kpi.models import KpiTarget
from app.kpi.schemas import KpiTargetOut, KpiTargetSetIn
from app.kpi.service import ScorecardService, resolve_overrides
from app.models.enums import UserRole, role_label
from app.models.operation import Operation, TaskAssignment
from app.models.bdn import VesselActivity
from app.models.user import User
from app.permissions import is_operation_manager
from app.schemas.common import StandardResponse

router = APIRouter(tags=["KPI"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


async def _require_operation_visible(operation_id: UUID, user: User, db: AsyncSession) -> None:
    """Mirrors OperationService._check_visibility.

    Deliberately re-stated rather than imported: that method reads
    `operation.task_assignments` off an already-eager-loaded operation, and
    this route loads nothing but the ids it needs. The rule itself is
    identical and must stay that way — if operation visibility changes, this
    changes with it.
    """
    if is_operation_manager(user) or user.role == UserRole.finance_manager:
        return

    operation = (await db.execute(
        select(Operation).where(Operation.id == operation_id, Operation.deleted_at.is_(None))
    )).scalars().first()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

    if user.role == UserRole.client:
        # A client's own delivery-performance view is a separate, much
        # narrower surface in the portal; the internal scorecard is not it.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    assigned = (await db.execute(
        select(TaskAssignment.assigned_to).where(TaskAssignment.operation_id == operation_id)
    )).scalars().all()
    if user.id in set(assigned):
        return

    # Marine Operations reach an operation through a vessel activity rather
    # than a task assignment, exactly as they do on the operation itself.
    on_vessel = (await db.execute(
        select(VesselActivity.id).where(
            VesselActivity.operation_id == operation_id,
            VesselActivity.assigned_to == user.id,
        )
    )).scalars().first()
    if on_vessel:
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


@router.get("/kpi/operations/{operation_id}/scorecard", response_model=StandardResponse)
async def get_operation_scorecard(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The operation's full report card: every metric, its value, its score,
    and the job document the target came from."""
    await _require_operation_visible(operation_id, current_user, db)
    scorecard = await ScorecardService.get_operation_scorecard(operation_id, current_user, db)
    return StandardResponse.ok(data=scorecard.model_dump(mode="json"))


@router.get("/kpi/targets", response_model=StandardResponse)
async def list_targets(
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Every metric the system scores, what it is currently held to, and what
    the catalog default was before anyone changed it."""
    overrides = await resolve_overrides(db)

    out: List[KpiTargetOut] = []
    for metric in CATALOG.values():
        override = overrides.get(metric.key)
        out.append(KpiTargetOut(
            metric_key=metric.key,
            label=metric.label,
            unit=metric.unit,
            role=metric.role.value,
            role_label=role_label(metric.role),
            curve=metric.curve.value,
            weight=(override.weight if override and override.weight is not None else metric.weight),
            critical=metric.critical,
            doc=metric.doc,
            description=metric.description,
            effective_target=(override.target if override and override.target is not None else metric.target),
            effective_fail=(override.fail if override and override.fail is not None else metric.fail),
            default_target=metric.target,
            default_fail=metric.fail,
            is_overridden=bool(override),
            overridden_at=override.at if override else None,
            overridden_by=override.by if override else None,
            override_reason=override.reason if override else None,
        ))

    out.sort(key=lambda t: (t.role_label, not t.critical, t.label))
    return StandardResponse.ok(data=[t.model_dump(mode="json") for t in out])


@router.put("/kpi/targets", response_model=StandardResponse)
async def set_target(
    payload: KpiTargetSetIn,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Retune one metric.

    This inserts rather than updates. The table is the audit trail: it keeps
    who changed a target, when it took effect, and why — so a score can
    always be read back against the target that was actually in force at the
    time, and a retune never silently rewrites last month's results.
    """
    metric: Optional[MetricDef] = CATALOG.get(payload.metric_key)
    if not metric:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown metric '{payload.metric_key}'",
        )

    if payload.target_value is None and payload.fail_value is None \
            and payload.weight is None and payload.is_active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to change — provide a target, fail point, weight, or active flag",
        )

    # A zero-width band would make every value score either 100 or 0 with
    # nothing in between, and for lower_better a fail below the target
    # inverts the curve so worse performance scores higher.
    target = payload.target_value if payload.target_value is not None else metric.target
    fail = payload.fail_value if payload.fail_value is not None else metric.fail
    if metric.curve.value == "lower_better" and fail <= target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The fail point must be higher than the target for a lower-is-better metric",
        )
    if metric.curve.value == "higher_better" and fail >= target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The fail point must be lower than the target for a higher-is-better metric",
        )
    if payload.weight is not None and not (0 < payload.weight <= 1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Weight must be greater than 0 and no more than 1",
        )

    row = KpiTarget(
        metric_key=payload.metric_key,
        target_value=payload.target_value,
        fail_value=payload.fail_value,
        weight=payload.weight,
        is_active=True if payload.is_active is None else payload.is_active,
        effective_from=payload.effective_from or datetime.now(timezone.utc),
        set_by=current_user.id,
        reason=payload.reason.strip(),
    )
    db.add(row)
    await db.flush()

    return StandardResponse.ok(
        data={"metric_key": row.metric_key, "effective_from": row.effective_from.isoformat()},
        message=f"Target updated for {metric.label}",
    )
