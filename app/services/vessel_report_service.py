"""Vessel report — one barge over a date range — and the per-vessel counts
on the Vessels list (Required System Update, item 1 C and D).

Read only. Figures are printed exactly as recorded: nothing is converted, and
a value that was never recorded comes back as None so the page shows a blank
(the unit rule, RAOMS-SYSTEM-REFERENCE.md §2). The vessel-side quantity
columns have a known scale problem (KPI-WORKLOG.md, 2026-08 item 4) that is
not this report's to fix; it reports what is on file.

No money: the Safety Officer reads this report, and they do not see finance.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bdn import BDN, RobEntry, VesselActivity, VesselActivityLeg
from app.models.enums import AuditResult, BdnStatus
from app.models.operation import Operation
from app.models.sts_collection import StsCollection, STS_HSE_PHASES
from app.models.user import User
from app.models.vessel import Vessel
from app.models.vessel_record import VesselIncident, VesselMaintenance
from app.services.vessel_record_service import VesselRecordService, due_items

# Longest period one report may cover — a guard against an accidental
# "since 2000" pulling every record on file into one page.
MAX_REPORT_DAYS = 366 * 3


def _s(v) -> Optional[str]:
    """A recorded number as text, or None when it was never recorded."""
    return None if v is None else str(v)


def _iso(v) -> Optional[str]:
    return v.isoformat() if v else None


def _result(v) -> Optional[str]:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _bounds(date_from: date, date_to: date):
    if date_to < date_from:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The end date is before the start date")
    if (date_to - date_from).days > MAX_REPORT_DAYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A report can cover at most three years")
    start = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def _in(ts: Optional[datetime], start: datetime, end: datetime) -> bool:
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return start <= ts < end


def _uuids(ids: Iterable) -> set:
    out = set()
    for i in ids:
        try:
            out.add(UUID(str(i)))
        except (TypeError, ValueError):
            pass
    return out


async def _names(db: AsyncSession, ids: Iterable) -> Dict[str, str]:
    ids = _uuids(i for i in ids if i)
    if not ids:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {str(r.id): r.full_name for r in rows}


async def _op_numbers(db: AsyncSession, ids: Iterable) -> Dict[str, str]:
    ids = _uuids(i for i in ids if i)
    if not ids:
        return {}
    rows = await db.execute(select(Operation.id, Operation.operation_number).where(Operation.id.in_(ids)))
    return {str(r.id): r.operation_number for r in rows}


class VesselReportService:

    @staticmethod
    async def build_report(vessel_id: UUID, date_from: date, date_to: date,
                           current_user: User, db: AsyncSession) -> Dict[str, Any]:
        vessel = await db.get(Vessel, vessel_id)
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")
        start, end = _bounds(date_from, date_to)

        deliveries = await VesselReportService._deliveries(vessel_id, start, end, db)
        rob = await VesselReportService._rob(vessel_id, start, end, db)
        hse = await VesselReportService._hse(vessel_id, start, end, db)
        incidents = await VesselReportService._incidents(vessel_id, start, end, db)
        maintenance = await VesselReportService._maintenance(vessel_id, date_from, date_to, db)

        approved = [d for d in deliveries if d["status"] == BdnStatus.approved.value]
        with_mt = [Decimal(d["mt_vacuum"]) for d in approved if d["mt_vacuum"] is not None]
        open_now = (await db.execute(
            select(func.count()).select_from(VesselIncident).where(and_(
                VesselIncident.vessel_id == vessel_id, VesselIncident.status != "closed",
            ))
        )).scalar_one()

        return {
            "vessel": {
                "id": str(vessel.id),
                "vessel_name": vessel.vessel_name,
                "imo_number": vessel.imo_number,
                "vessel_type": vessel.vessel_type,
                "flag_state": vessel.flag_state,
                "status": vessel.status.value,
            },
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "generated_by_name": current_user.full_name,
            "summary": {
                "deliveries": len(deliveries),
                "deliveries_approved": len(approved),
                # Approved BDNs only; those with no MT(vac) on file are counted, not guessed.
                "approved_mt_vacuum": str(sum(with_mt)) if with_mt else None,
                "approved_missing_mt_vacuum": len(approved) - len(with_mt),
                "rob_start": rob["rob_start"],
                "rob_end": rob["rob_end"],
                "hse_checks": len(hse),
                "hse_failed": sum(1 for h in hse if h["result"] == AuditResult.not_satisfactory.value),
                "incidents": len(incidents),
                "incidents_by_severity": {
                    s: sum(1 for i in incidents if i["severity"] == s)
                    for s in ("low", "medium", "high", "critical")
                },
                "incidents_open_now": open_now,
                "maintenance_done": sum(1 for m in maintenance["records"] if m["status"] == "done"),
                "maintenance_overdue_now": sum(1 for d in maintenance["due"] if d["state"] == "overdue"),
                "maintenance_due_soon_now": sum(1 for d in maintenance["due"] if d["state"] == "due_soon"),
            },
            "deliveries": deliveries,
            "rob_movements": rob["entries"],
            "hse_checks": hse,
            "incidents": incidents,
            "maintenance": maintenance["records"],
            "maintenance_due": maintenance["due"],
        }

    @staticmethod
    async def _deliveries(vessel_id, start, end, db) -> List[dict]:
        result = await db.execute(
            select(BDN)
            .options(selectinload(BDN.operation))
            .where(and_(BDN.vessel_id == vessel_id, BDN.delivery_date >= start, BDN.delivery_date < end))
            .order_by(BDN.delivery_date)
        )
        return [{
            "bdn_number": b.bdn_number,
            "delivery_date": _iso(b.delivery_date),
            "operation_id": str(b.operation_id),
            "operation_number": b.operation.operation_number if b.operation else None,
            "receiving_vessel": b.receiving_vessel,
            "company_name": b.company_name,
            "product_type": b.product_type,
            "status": b.status.value,
            "gov": _s(b.discharge_gov),
            "gsv": _s(b.discharge_gsv),
            "mt_vacuum": _s(b.discharge_mt_vacuum),
            "density": _s(b.density),
            "temperature": _s(b.temperature),
            "vcf": _s(b.vcf),
        } for b in result.scalars().all()]

    @staticmethod
    async def _rob(vessel_id, start, end, db) -> Dict[str, Any]:
        before = (await db.execute(
            select(RobEntry.rob_after_mt)
            .where(and_(RobEntry.vessel_id == vessel_id, RobEntry.created_at < start))
            .order_by(RobEntry.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        result = await db.execute(
            select(RobEntry)
            .options(selectinload(RobEntry.recorder), selectinload(RobEntry.operation))
            .where(and_(RobEntry.vessel_id == vessel_id, RobEntry.created_at >= start, RobEntry.created_at < end))
            .order_by(RobEntry.created_at)
        )
        entries = list(result.scalars().all())
        rob_start = before if before is not None else (entries[0].rob_before_mt if entries else None)
        rob_end = entries[-1].rob_after_mt if entries else before
        return {
            "rob_start": _s(rob_start),
            "rob_end": _s(rob_end),
            "entries": [{
                "created_at": _iso(e.created_at),
                "entry_type": e.entry_type.value,
                "quantity": _s(e.quantity_mt),
                "rob_before": _s(e.rob_before_mt),
                "rob_after": _s(e.rob_after_mt),
                "operation_number": e.operation.operation_number if e.operation else None,
                "description": e.source_description or e.notes,
                "recorded_by_name": e.recorder.full_name if e.recorder else None,
            } for e in entries],
        }

    @staticmethod
    async def _hse(vessel_id, start, end, db) -> List[dict]:
        """Every HSE check done on this barge in the period: the three phases
        of each vessel run, each delivery leg's check, and each ship-to-ship
        collection's phases. "Not satisfactory" is what the KPI counts as a
        safety incident (kpi/service.py) — the same reading here.

        vessel_id None reads the whole fleet (the safety dashboard)."""
        rows: List[dict] = []
        # Only runs with a check inside the period: on the run itself or on
        # one of its delivery legs. Keeps the fleet-wide read small.
        leg_in_period = select(VesselActivityLeg.vessel_activity_id).where(and_(
            VesselActivityLeg.hse_conducted_at >= start, VesselActivityLeg.hse_conducted_at < end,
        ))
        act_where = [
            VesselActivity.created_at < end,
            or_(
                and_(VesselActivity.hse_conducted_at >= start, VesselActivity.hse_conducted_at < end),
                and_(VesselActivity.hse_during_conducted_at >= start, VesselActivity.hse_during_conducted_at < end),
                and_(VesselActivity.hse_post_conducted_at >= start, VesselActivity.hse_post_conducted_at < end),
                VesselActivity.id.in_(leg_in_period),
            ),
        ]
        sts_where = [StsCollection.created_at < end]
        if vessel_id is not None:
            act_where.append(VesselActivity.vessel_id == vessel_id)
            sts_where.append(StsCollection.vessel_id == vessel_id)

        acts = (await db.execute(
            select(VesselActivity)
            .options(selectinload(VesselActivity.legs))
            # A run created after the period cannot hold a check inside it.
            .where(and_(*act_where))
        )).scalars().all()
        for a in acts:
            for phase, prefix in (("pre", "hse"), ("during", "hse_during"), ("post", "hse_post")):
                at = getattr(a, f"{prefix}_conducted_at")
                if _in(at, start, end):
                    rows.append({
                        "conducted_at": _iso(at),
                        "source": f"Vessel run {a.activity_number}",
                        "vessel_id": str(a.vessel_id),
                        "operation_id": str(a.operation_id),
                        "phase": phase,
                        "result": _result(getattr(a, f"{prefix}_result")),
                        "safety_officer": getattr(a, f"{prefix}_safety_officer"),
                        "conducted_by": getattr(a, f"{prefix}_conducted_by"),
                        "notes": getattr(a, f"{prefix}_notes"),
                    })
            for leg in a.legs:
                if _in(leg.hse_conducted_at, start, end):
                    rows.append({
                        "conducted_at": _iso(leg.hse_conducted_at),
                        "source": f"Delivery to {leg.receiving_vessel_name} ({a.activity_number})",
                        "vessel_id": str(a.vessel_id),
                        "operation_id": str(a.operation_id),
                        "phase": "pre",
                        "result": _result(leg.hse_result),
                        "safety_officer": leg.hse_safety_officer,
                        "conducted_by": leg.hse_conducted_by,
                        "notes": leg.hse_notes,
                    })

        stss = (await db.execute(
            select(StsCollection).where(and_(*sts_where))
        )).scalars().all()
        for s in stss:
            for phase in STS_HSE_PHASES:
                check = (s.hse or {}).get(phase) or {}
                at = check.get("conducted_at")
                at_dt = datetime.fromisoformat(at) if isinstance(at, str) else None
                if _in(at_dt, start, end):
                    rows.append({
                        "conducted_at": at,
                        "source": f"Ship-to-ship from {s.source_vessel_name}",
                        "vessel_id": str(s.vessel_id),
                        "operation_id": str(s.operation_id),
                        "phase": phase,
                        "result": check.get("result"),
                        "safety_officer": check.get("safety_officer"),
                        "conducted_by": check.get("conducted_by"),
                        "conducted_by_name": check.get("conducted_by_name"),
                        "notes": check.get("notes"),
                    })

        names = await _names(db, (r["conducted_by"] for r in rows))
        ops = await _op_numbers(db, (r["operation_id"] for r in rows))
        for r in rows:
            r["conducted_by_name"] = names.get(str(r.pop("conducted_by") or "")) or r.get("conducted_by_name")
            r["operation_number"] = ops.get(r["operation_id"])
        rows.sort(key=lambda r: r["conducted_at"] or "")
        return rows

    @staticmethod
    async def _incidents(vessel_id, start, end, db) -> List[dict]:
        result = await db.execute(
            VesselRecordService._incident_query()
            .where(and_(
                VesselIncident.vessel_id == vessel_id,
                VesselIncident.occurred_at >= start,
                VesselIncident.occurred_at < end,
            ))
            .order_by(VesselIncident.occurred_at)
        )
        return [VesselRecordService._incident_out(i) for i in result.scalars().all()]

    @staticmethod
    async def _maintenance(vessel_id, date_from: date, date_to: date, db) -> Dict[str, Any]:
        """Work planned, done or logged in the period; what is due is as of today."""
        result = await db.execute(
            select(VesselMaintenance)
            .options(selectinload(VesselMaintenance.creator))
            .where(VesselMaintenance.vessel_id == vessel_id)
        )
        everything = list(result.scalars().all())

        def touches(m) -> bool:
            created = m.created_at.date() if m.created_at else None
            return any(d is not None and date_from <= d <= date_to
                       for d in (m.planned_date, m.completed_date, created))

        in_period = sorted((m for m in everything if touches(m)),
                           key=lambda m: m.completed_date or m.planned_date or m.created_at.date())
        return {
            "records": [VesselRecordService._maintenance_out(m) for m in in_period],
            "due": due_items(everything, date.today()),
        }

    # ── Vessels list counts ────────────────────────────────────────────────────

    @staticmethod
    async def records_summary(db: AsyncSession) -> Dict[str, Dict[str, int]]:
        """Open incidents and overdue / due-soon maintenance for every vessel
        that has any, keyed by vessel id."""
        out: Dict[str, Dict[str, int]] = {}

        def slot(vid) -> Dict[str, int]:
            return out.setdefault(str(vid), {"open_incidents": 0, "overdue_maintenance": 0, "due_soon_maintenance": 0})

        rows = await db.execute(
            select(VesselIncident.vessel_id, func.count())
            .where(VesselIncident.status != "closed")
            .group_by(VesselIncident.vessel_id)
        )
        for vid, n in rows:
            slot(vid)["open_incidents"] = n

        records = (await db.execute(select(VesselMaintenance))).scalars().all()
        by_vessel: Dict[str, list] = {}
        for m in records:
            by_vessel.setdefault(str(m.vessel_id), []).append(m)
        today = date.today()
        for vid, recs in by_vessel.items():
            due = due_items(recs, today)
            overdue = sum(1 for d in due if d["state"] == "overdue")
            soon = len(due) - overdue
            if overdue or soon:
                s = slot(vid)
                s["overdue_maintenance"] = overdue
                s["due_soon_maintenance"] = soon
        return out
