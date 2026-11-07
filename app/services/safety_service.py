"""The safety dashboard: what needs the Safety Officer's attention, fleet-wide.

Read only. Every figure comes from records other parts of the system already
keep — vessel incidents and maintenance, HSE checks on vessel runs, truck
safety audits, truck issues — plus the inspection log.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import AuditPhase, AuditResult, OperationStatus, TruckIssueStatus, TruckOpStatus
from app.models.inspection import Inspection, InspectionAction
from app.models.operation import Operation
from app.models.truck import Truck, TruckIssue, TruckOperation, TruckSafetyAudit
from app.models.user import User
from app.models.vessel import Vessel
from app.models.vessel_record import INCIDENT_SEVERITIES, VesselIncident, VesselMaintenance
from app.services.vessel_record_service import due_items
from app.services.vessel_report_service import VesselReportService, _iso

# How far back "failed checks" looks.
RECENT_DAYS = 30

# Operations whose trucks are still on the move or discharging.
LIVE_OPERATION_STATUSES = (
    OperationStatus.active, OperationStatus.pending_completion, OperationStatus.vessel_operations,
)

# A truck's Pre check is due once it reaches the jetty; its Post check once
# discharge has ended (AuditPhase: pre / post are the jetty checks).
_AT_JETTY = (TruckOpStatus.arrived, TruckOpStatus.discharging, TruckOpStatus.completed)


def missing_audits(truck_op, phases_done: set) -> List[str]:
    """Which of Pre and Post this truck should have by now but does not."""
    missing = []
    reached_jetty = truck_op.arrived_discharge_at is not None or truck_op.status in _AT_JETTY
    discharged = truck_op.discharge_end_at is not None or truck_op.status == TruckOpStatus.completed
    if reached_jetty and AuditPhase.pre not in phases_done:
        missing.append("pre")
    if discharged and AuditPhase.post not in phases_done:
        missing.append("post")
    return missing


def _age_days(ts, today: date) -> int:
    d = ts.date() if isinstance(ts, datetime) else ts
    return max((today - d).days, 0)


class SafetyService:

    @staticmethod
    async def overview(db: AsyncSession) -> Dict[str, Any]:
        today = date.today()
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=RECENT_DAYS)
        vessel_names = dict((await db.execute(select(Vessel.id, Vessel.vessel_name))).all())

        return {
            "generated_at": now.isoformat(),
            "recent_days": RECENT_DAYS,
            "incidents": await SafetyService._incidents(db, vessel_names, today),
            "failed_checks": await SafetyService._failed_checks(db, vessel_names, since, now),
            "missing_truck_audits": await SafetyService._missing_truck_audits(db),
            "open_truck_issues": await SafetyService._open_truck_issues(db, today),
            "maintenance_due": await SafetyService._maintenance_due(db, vessel_names, today),
            "inspections": await SafetyService._inspections(db, today),
        }

    @staticmethod
    async def _incidents(db, vessel_names, today) -> Dict[str, Any]:
        rows = (await db.execute(
            select(VesselIncident).where(VesselIncident.status != "closed")
        )).scalars().all()
        by_severity = {s: 0 for s in INCIDENT_SEVERITIES}
        open_items = []
        for i in rows:
            by_severity[i.severity] = by_severity.get(i.severity, 0) + 1
            open_items.append({
                "id": str(i.id),
                "vessel_id": str(i.vessel_id),
                "vessel_name": vessel_names.get(i.vessel_id),
                "title": i.title,
                "incident_type": i.incident_type,
                "severity": i.severity,
                "status": i.status,
                "occurred_at": _iso(i.occurred_at),
                "age_days": _age_days(i.occurred_at, today),
            })
        rank = {s: n for n, s in enumerate(reversed(INCIDENT_SEVERITIES))}
        open_items.sort(key=lambda r: (rank.get(r["severity"], 99), r["occurred_at"]))
        return {"open": open_items, "by_severity": by_severity}

    @staticmethod
    async def _failed_checks(db, vessel_names, since, now) -> Dict[str, Any]:
        """Vessel HSE checks and truck safety audits marked not satisfactory."""
        hse = await VesselReportService._hse(None, since, now, db)
        failed = [
            {
                "kind": "vessel",
                "conducted_at": r["conducted_at"],
                "vessel_id": r["vessel_id"],
                "subject": vessel_names.get(_uuid(r["vessel_id"])),
                "source": r["source"],
                "operation_id": r["operation_id"],
                "operation_number": r.get("operation_number"),
                "phase": r["phase"],
                "notes": r.get("notes"),
                "conducted_by_name": r.get("conducted_by_name"),
            }
            for r in hse if r["result"] == AuditResult.not_satisfactory.value
        ]

        # Columns only: an audit row carries its whole checklist as JSON.
        in_period = TruckSafetyAudit.conducted_at >= since
        truck_total = (await db.execute(
            select(func.count()).select_from(TruckSafetyAudit).where(in_period)
        )).scalar_one()
        truck_failed = (await db.execute(
            select(
                TruckSafetyAudit.conducted_at, TruckSafetyAudit.phase, TruckSafetyAudit.notes,
                TruckSafetyAudit.conductor_name, TruckSafetyAudit.truck_id, TruckSafetyAudit.operation_id,
                Truck.truck_number, Operation.operation_number, User.full_name,
            )
            .join(Truck, Truck.id == TruckSafetyAudit.truck_id)
            .join(Operation, Operation.id == TruckSafetyAudit.operation_id)
            .outerjoin(User, User.id == TruckSafetyAudit.conducted_by)
            .where(and_(in_period, TruckSafetyAudit.result == AuditResult.not_satisfactory))
        )).all()
        for a in truck_failed:
            failed.append({
                "kind": "truck",
                "conducted_at": _iso(a.conducted_at),
                "truck_id": str(a.truck_id),
                "subject": a.truck_number,
                "source": "Truck safety audit",
                "operation_id": str(a.operation_id),
                "operation_number": a.operation_number,
                "phase": a.phase.value,
                "notes": a.notes,
                "conducted_by_name": a.conductor_name or a.full_name,
            })
        failed.sort(key=lambda r: r["conducted_at"] or "", reverse=True)
        return {"total_checks": len(hse) + truck_total, "failed": failed}

    @staticmethod
    async def _missing_truck_audits(db) -> List[Dict[str, Any]]:
        # Columns only: loading an Operation pulls its clearances and products.
        truck_ops = (await db.execute(
            select(
                TruckOperation.id, TruckOperation.truck_id, TruckOperation.operation_id,
                TruckOperation.status, TruckOperation.arrived_discharge_at, TruckOperation.discharge_end_at,
                TruckOperation.driver_name, Truck.truck_number, Operation.operation_number,
            )
            .join(Operation, Operation.id == TruckOperation.operation_id)
            .join(Truck, Truck.id == TruckOperation.truck_id)
            .where(and_(
                Operation.deleted_at.is_(None),
                Operation.status.in_(LIVE_OPERATION_STATUSES),
                TruckOperation.status != TruckOpStatus.cancelled,
            ))
        )).all()
        phases = defaultdict(set)
        if truck_ops:
            # Which phases each truck has, without loading the checklists.
            for truck_op_id, phase in (await db.execute(
                select(TruckSafetyAudit.truck_op_id, TruckSafetyAudit.phase)
                .where(TruckSafetyAudit.truck_op_id.in_([t.id for t in truck_ops]))
            )).all():
                phases[truck_op_id].add(phase)
        out = []
        for t in truck_ops:
            missing = missing_audits(t, phases[t.id])
            if not missing:
                continue
            out.append({
                "truck_op_id": str(t.id),
                "truck_id": str(t.truck_id),
                "truck_number": t.truck_number,
                "driver_name": t.driver_name,
                "operation_id": str(t.operation_id),
                "operation_number": t.operation_number,
                "truck_status": t.status.value,
                "missing": missing,
            })
        out.sort(key=lambda r: (r["operation_number"] or "", r["truck_number"] or ""))
        return out

    @staticmethod
    async def _open_truck_issues(db, today) -> List[Dict[str, Any]]:
        issues = (await db.execute(
            select(
                TruckIssue.id, TruckIssue.truck_id, TruckIssue.title, TruckIssue.severity,
                TruckIssue.created_at, Truck.truck_number, Operation.operation_number,
            )
            .join(Truck, Truck.id == TruckIssue.truck_id)
            .outerjoin(Operation, Operation.id == TruckIssue.operation_id)
            .where(TruckIssue.status == TruckIssueStatus.open)
            .order_by(TruckIssue.created_at)
        )).all()
        return [
            {
                "id": str(i.id),
                "truck_id": str(i.truck_id),
                "truck_number": i.truck_number,
                "title": i.title,
                "severity": i.severity.value,
                "operation_number": i.operation_number,
                "created_at": _iso(i.created_at),
                "age_days": _age_days(i.created_at, today),
            }
            for i in issues
        ]

    @staticmethod
    async def _maintenance_due(db, vessel_names, today) -> List[Dict[str, Any]]:
        records = (await db.execute(select(VesselMaintenance))).scalars().all()
        per_vessel = defaultdict(list)
        for m in records:
            per_vessel[m.vessel_id].append(m)
        out = []
        for vessel_id, items in per_vessel.items():
            for d in due_items(items, today):
                out.append({**d, "vessel_id": str(vessel_id), "vessel_name": vessel_names.get(vessel_id)})
        out.sort(key=lambda d: d["due_date"])
        return out

    @staticmethod
    async def _inspections(db, today) -> Dict[str, Any]:
        month_start = today.replace(day=1)
        this_month = (await db.execute(
            select(Inspection.result).where(Inspection.inspected_on >= month_start)
        )).scalars().all()
        actions = (await db.execute(
            select(InspectionAction)
            .options(selectinload(InspectionAction.owner), selectinload(InspectionAction.inspection))
            .where(InspectionAction.status == "open")
            .order_by(InspectionAction.due_date)
        )).scalars().all()
        return {
            "this_month": len(this_month),
            "failed_this_month": sum(1 for r in this_month if r == "fail"),
            "open_actions": [
                {
                    "id": str(a.id),
                    "inspection_id": str(a.inspection_id),
                    "inspection_title": a.inspection.title if a.inspection else None,
                    "description": a.description,
                    "owner_name": a.owner.full_name if a.owner else None,
                    "due_date": a.due_date.isoformat(),
                    "overdue": a.due_date < today,
                }
                for a in actions
            ],
        }


def _uuid(v):
    try:
        return UUID(str(v))
    except (TypeError, ValueError):
        return None

