from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditLog
from app.models.bdn import VesselActivity
from app.models.enums import VesselActivityStatus, VesselStage
from app.models.user import User
from app.schemas.kpi import OperationKpiOut, RoleStageDurationsOut, StageDurationEntry, VesselRunKpi

_STAGE_ORDER = [s.value for s in VesselStage]


def _ensure_aware(dt: datetime) -> datetime:
    """occurred_at is caller-supplied with no tz requirement — normalize a
    naive value to UTC so mixing naive/aware timestamps for the same
    activity never crashes the subtraction below."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _hours_between(earlier: datetime, later: datetime) -> float:
    return (_ensure_aware(later) - _ensure_aware(earlier)).total_seconds() / 3600


class KpiService:
    """Computed on the fly from data the vessel-operations build already
    produces — no new tables. Deliberately minimal: only reports what the
    existing stage timestamps and audit trail actually contain."""

    @staticmethod
    async def get_operation_kpi(operation_id: UUID, db: AsyncSession) -> OperationKpiOut:
        stmt = (
            select(VesselActivity)
            .where(
                VesselActivity.operation_id == operation_id,
                VesselActivity.status != VesselActivityStatus.cancelled,
            )
            .options(selectinload(VesselActivity.vessel))
        )
        activities = (await db.execute(stmt)).scalars().all()

        runs: List[VesselRunKpi] = []
        cast_offs: List[datetime] = []
        completions: List[datetime] = []
        for a in activities:
            duration = (
                _hours_between(a.stage_cast_off_at, a.stage_discharge_completed_at)
                if a.stage_cast_off_at and a.stage_discharge_completed_at else None
            )
            runs.append(VesselRunKpi(
                vessel_activity_id=a.id,
                vessel_name=a.vessel.vessel_name if a.vessel else None,
                cast_off_at=a.stage_cast_off_at,
                discharge_completed_at=a.stage_discharge_completed_at,
                duration_hours=duration,
            ))
            if a.stage_cast_off_at:
                cast_offs.append(_ensure_aware(a.stage_cast_off_at))
            if a.stage_discharge_completed_at:
                completions.append(_ensure_aware(a.stage_discharge_completed_at))

        earliest = min(cast_offs) if cast_offs else None
        latest = max(completions) if completions else None
        overall_duration = _hours_between(earliest, latest) if earliest and latest else None

        return OperationKpiOut(
            operation_id=operation_id,
            cast_off_at=earliest,
            discharge_completed_at=latest,
            duration_hours=overall_duration,
            vessel_runs=runs,
        )

    @staticmethod
    async def get_role_stage_durations(operation_id: UUID, db: AsyncSession) -> RoleStageDurationsOut:
        activity_ids_stmt = select(VesselActivity.id).where(
            VesselActivity.operation_id == operation_id,
            VesselActivity.status != VesselActivityStatus.cancelled,
        )
        activity_ids = (await db.execute(activity_ids_stmt)).scalars().all()
        if not activity_ids:
            return RoleStageDurationsOut(operation_id=operation_id, entries=[])

        log_stmt = (
            select(AuditLog, User.role, User.full_name)
            .join(User, User.id == AuditLog.user_id)
            .where(
                AuditLog.action == "ADVANCE_VESSEL_STAGE",
                AuditLog.entity_id.in_(activity_ids),
            )
            .order_by(AuditLog.entity_id, AuditLog.created_at)
        )
        rows = (await db.execute(log_stmt)).all()

        # Re-logging an earlier stage (a correction) is explicitly allowed by
        # advance_stage and doesn't rewrite history — so the audit trail can
        # hold more than one row per (activity, stage). Rows are already
        # ordered oldest-first per activity; keeping the LAST one per stage
        # means a correction supersedes its own prior entry instead of
        # appearing as a spurious duplicate.
        by_activity_stage: Dict[UUID, Dict[str, tuple]] = {}
        for log, role, name in rows:
            changes = log.changes or {}
            stage_value = changes.get("stage", "unknown")
            occurred_at = datetime.fromisoformat(changes["occurred_at"]) if changes.get("occurred_at") else None
            by_activity_stage.setdefault(log.entity_id, {})[stage_value] = (occurred_at, role, name)

        entries: List[StageDurationEntry] = []
        for activity_id, stage_map in by_activity_stage.items():
            # Pair durations along the CANONICAL stage sequence, not raw audit
            # chronology — a correction to an earlier stage must diff against
            # the stage before it, never against whatever was logged most
            # recently in wall-clock time.
            prev_time = None
            for stage_value in _STAGE_ORDER:
                if stage_value not in stage_map:
                    continue
                occurred_at, role, name = stage_map[stage_value]
                duration = _hours_between(prev_time, occurred_at) if prev_time and occurred_at else None
                entries.append(StageDurationEntry(
                    vessel_activity_id=activity_id,
                    stage=stage_value,
                    role=role.value if role else None,
                    user_name=name,
                    started_at=prev_time,
                    completed_at=occurred_at,
                    duration_hours=duration,
                ))
                if occurred_at:
                    prev_time = occurred_at

        return RoleStageDurationsOut(operation_id=operation_id, entries=entries)
