"""Vessel incident log and maintenance log.

House rules apply to both: a record is never deleted or rewritten in place
without trace. Every change after the first write carries a written reason
and lands on the audit log with what changed. Attachments are only ever
added.
"""
import logging
import mimetypes
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.audit import AuditLog
from app.models.bdn import VesselActivity
from app.models.enums import UserRole
from app.models.operation import Operation
from app.models.user import User
from app.models.vessel import Vessel
from app.models.vessel_record import VesselIncident, VesselMaintenance
from app.permissions import INCIDENT_CLOSER_ROLES
from app.schemas.vessel_record import (
    VesselIncidentCreate, VesselIncidentUpdate, VesselIncidentInvestigate,
    VesselIncidentClose, VesselIncidentReopen,
    VesselMaintenanceCreate, VesselMaintenanceUpdate,
)
from app.services.audit_diff import capture_diff
from app.services.bdn_notify import active_users
from app.services.document_service import (
    ALLOWED_MIME_TYPES, MAX_FILE_SIZE_BYTES, _upload_to_supabase, create_signed_supabase_url,
)
from app.services.email_service import email_vessel_incident_reported
from app.services.notification_service import notify
from app.utils.filenames import display_name, storage_key_name

logger = logging.getLogger("raoms")

VESSEL_RECORDS_BUCKET = "vessel-records"

# How far ahead "due soon" looks on the maintenance log.
DUE_SOON_DAYS = 30

_INCIDENT_TYPE_LABELS = {
    "spill": "Spill", "injury": "Injury", "near_miss": "Near miss",
    "equipment_failure": "Equipment failure", "collision": "Collision or contact",
    "fire": "Fire", "pollution": "Pollution", "security": "Security", "other": "Other",
}

# Fields a record cannot be left without — a blanked one in an update is refused.
_INCIDENT_REQUIRED = ("occurred_at", "incident_type", "severity", "title")
_MAINTENANCE_REQUIRED = ("maintenance_type", "title", "status")


def _audit(user: User, action: str, entity_type: str, entity_id: UUID, changes: dict,
           meta: Optional[dict], operation_id: Optional[UUID] = None,
           reason: Optional[str] = None) -> AuditLog:
    return AuditLog(
        user_id=user.id,
        operation_id=operation_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=changes,
        reason=reason,
        acted_as_role=user.acting_as_role,
        ip_address=meta.get("ip") if meta else None,
        user_agent=meta.get("user_agent") if meta else None,
    )


def _refuse_blanked(update: Dict[str, Any], required: Iterable[str]) -> None:
    for field in required:
        if field in update and update[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field.replace('_', ' ').capitalize()} cannot be left empty",
            )


async def _vessel_or_404(vessel_id: UUID, db: AsyncSession) -> Vessel:
    vessel = await db.get(Vessel, vessel_id)
    if not vessel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")
    return vessel


def _attachments_out(items: Optional[list]) -> List[dict]:
    # The stored URL is the private storage path; a signed link is issued on
    # request (see attachment_link) rather than signing every file on every list.
    return [{k: v for k, v in a.items() if k != "file_url"} for a in (items or [])]


async def _store_attachment(file: UploadFile, folder: str, user: User) -> dict:
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit",
        )
    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Photos (JPEG, PNG, WebP), PDF, Word, Excel or text files only",
        )
    attachment_id = str(uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    storage_path = f"{folder}/{timestamp}_{attachment_id[:8]}_{storage_key_name(file.filename, 'file')}"
    file_url = await _upload_to_supabase(content, storage_path, mime_type, bucket=VESSEL_RECORDS_BUCKET)
    return {
        "id": attachment_id,
        "file_name": display_name(file.filename, "file"),
        "file_url": file_url,
        "mime_type": mime_type,
        "size_bytes": len(content),
        "uploaded_by": str(user.id),
        "uploaded_by_name": user.full_name,
        "uploaded_at": datetime.utcnow().isoformat(),
    }


async def _attachment_link(items: Optional[list], attachment_id: str) -> dict:
    for a in items or []:
        if a.get("id") == attachment_id:
            url = await create_signed_supabase_url(a.get("file_url"), bucket=VESSEL_RECORDS_BUCKET)
            return {"url": url, "file_name": a.get("file_name"), "mime_type": a.get("mime_type")}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")


# ── Maintenance due dates ──────────────────────────────────────────────────────

def _series_key(record) -> tuple:
    return (record.maintenance_type, " ".join((record.title or "").lower().split()))


def _record_date(record) -> date:
    created = record.created_at.date() if record.created_at else date.min
    return record.completed_date or record.planned_date or created


def due_items(records: Iterable, today: date, soon_days: int = DUE_SOON_DAYS) -> List[dict]:
    """What is overdue or due soon on one vessel's maintenance log.

    Records with the same type and title are one series. Only the newest in
    each series counts, so logging this year's service clears last year's
    reminder:
      * planned / in progress -> due on its planned date
      * done                  -> due on its next due date (a certificate's expiry)
      * cancelled             -> nothing due
    """
    latest: Dict[tuple, Any] = {}
    for r in records:
        key = _series_key(r)
        current = latest.get(key)
        if current is None or (_record_date(r), r.created_at) > (_record_date(current), current.created_at):
            latest[key] = r

    out = []
    for r in latest.values():
        if r.status in ("planned", "in_progress"):
            due = r.planned_date
        elif r.status == "done":
            due = r.next_due_date
        else:
            due = None
        if due is None:
            continue
        if due < today:
            state = "overdue"
        elif due <= today + timedelta(days=soon_days):
            state = "due_soon"
        else:
            continue
        out.append({
            "record_id": str(r.id),
            "maintenance_type": r.maintenance_type,
            "title": r.title,
            "status": r.status,
            "due_date": due.isoformat(),
            "days": (due - today).days,
            "state": state,
        })
    out.sort(key=lambda d: d["due_date"])
    return out


class VesselRecordService:

    # ── Incidents ──────────────────────────────────────────────────────────────

    @staticmethod
    def _incident_out(i: VesselIncident) -> Dict[str, Any]:
        """Requires .reporter/.closer/.operation/.vessel_activity eager-loaded."""
        return {
            "id": str(i.id),
            "vessel_id": str(i.vessel_id),
            "operation_id": str(i.operation_id) if i.operation_id else None,
            "operation_number": i.operation.operation_number if i.operation else None,
            "vessel_activity_id": str(i.vessel_activity_id) if i.vessel_activity_id else None,
            "activity_number": i.vessel_activity.activity_number if i.vessel_activity else None,
            "occurred_at": i.occurred_at.isoformat(),
            "location": i.location,
            "incident_type": i.incident_type,
            "severity": i.severity,
            "title": i.title,
            "description": i.description,
            "immediate_action": i.immediate_action,
            "status": i.status,
            "root_cause": i.root_cause,
            "corrective_action": i.corrective_action,
            "reported_by": str(i.reported_by),
            "reported_by_name": i.reporter.full_name if i.reporter else None,
            "closed_by": str(i.closed_by) if i.closed_by else None,
            "closed_by_name": i.closer.full_name if i.closer else None,
            "closed_at": i.closed_at.isoformat() if i.closed_at else None,
            "attachments": _attachments_out(i.attachments),
            "created_at": i.created_at.isoformat(),
            "updated_at": i.updated_at.isoformat() if i.updated_at else None,
        }

    @staticmethod
    def _incident_query():
        return select(VesselIncident).options(
            selectinload(VesselIncident.reporter),
            selectinload(VesselIncident.closer),
            selectinload(VesselIncident.operation),
            selectinload(VesselIncident.vessel_activity),
        )

    @staticmethod
    async def _get_incident(incident_id: UUID, db: AsyncSession) -> VesselIncident:
        result = await db.execute(
            VesselRecordService._incident_query()
            .options(selectinload(VesselIncident.vessel))
            .where(VesselIncident.id == incident_id)
        )
        incident = result.scalar_one_or_none()
        if not incident:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
        return incident

    @staticmethod
    async def _reload_incident(incident: VesselIncident, db: AsyncSession) -> Dict[str, Any]:
        await db.flush()
        await db.refresh(incident, attribute_names=["reporter", "closer", "operation", "vessel_activity"])
        return VesselRecordService._incident_out(incident)

    @staticmethod
    async def list_incidents(vessel_id: UUID, db: AsyncSession) -> List[Dict[str, Any]]:
        await _vessel_or_404(vessel_id, db)
        result = await db.execute(
            VesselRecordService._incident_query()
            .where(VesselIncident.vessel_id == vessel_id)
            .order_by(VesselIncident.occurred_at.desc())
        )
        return [VesselRecordService._incident_out(i) for i in result.scalars().all()]

    @staticmethod
    async def create_incident(
        vessel_id: UUID,
        data: VesselIncidentCreate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        vessel = await _vessel_or_404(vessel_id, db)

        operation = None
        if data.operation_number:
            result = await db.execute(
                select(Operation).where(and_(
                    Operation.operation_number == data.operation_number.upper(),
                    Operation.deleted_at.is_(None),
                ))
            )
            operation = result.scalar_one_or_none()
            if not operation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No operation numbered {data.operation_number}",
                )

        activity = None
        if data.activity_number:
            result = await db.execute(
                select(VesselActivity).where(VesselActivity.activity_number == data.activity_number.upper())
            )
            activity = result.scalar_one_or_none()
            if not activity:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No vessel run numbered {data.activity_number}",
                )
            if activity.vessel_id != vessel.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Vessel run {activity.activity_number} was not on {vessel.vessel_name}",
                )
            if operation and activity.operation_id != operation.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Vessel run {activity.activity_number} belongs to a different operation",
                )
            if operation is None:
                operation = await db.get(Operation, activity.operation_id)

        if data.occurred_at.replace(tzinfo=None) > datetime.utcnow() + timedelta(minutes=10):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An incident cannot be dated in the future",
            )

        incident = VesselIncident(
            vessel_id=vessel.id,
            operation_id=operation.id if operation else None,
            vessel_activity_id=activity.id if activity else None,
            occurred_at=data.occurred_at,
            location=data.location,
            incident_type=data.incident_type,
            severity=data.severity,
            title=data.title,
            description=data.description,
            immediate_action=data.immediate_action,
            status="open",
            reported_by=current_user.id,
            attachments=[],
        )
        db.add(incident)
        await db.flush()

        db.add(_audit(
            current_user, "REPORT_VESSEL_INCIDENT", "vessel_incident", incident.id,
            {
                "vessel_name": vessel.vessel_name,
                "incident_type": data.incident_type,
                "severity": data.severity,
                "title": data.title,
            },
            request_meta, operation_id=incident.operation_id,
        ))

        await VesselRecordService._alert_incident(incident, vessel, operation, current_user, db)
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def _alert_incident(
        incident: VesselIncident,
        vessel: Vessel,
        operation: Optional[Operation],
        reporter: User,
        db: AsyncSession,
    ) -> None:
        """In-app and email to every active BM and Safety Officer, except the
        person who logged it."""
        recipients = [u for u in await active_users(db, INCIDENT_CLOSER_ROLES) if u.id != reporter.id]
        if not recipients:
            return
        severity = incident.severity.capitalize()
        type_label = _INCIDENT_TYPE_LABELS.get(incident.incident_type, incident.incident_type)
        path = f"/fleet/vessels/{vessel.id}?tab=incidents"
        message = f"{severity} {type_label.lower()} on {vessel.vessel_name}: {incident.title}. Logged by {reporter.full_name}."
        for user in recipients:
            await notify(
                db=db,
                user_id=user.id,
                type_="system",
                title=f"Incident on {vessel.vessel_name}",
                message=message,
                priority="high" if incident.severity in ("high", "critical") else "normal",
                operation_id=operation.id if operation else None,
                action_url=path,
                channels=["in_app"],
            )
            try:
                await email_vessel_incident_reported(
                    to_email=user.email,
                    recipient_name=user.full_name,
                    vessel_name=vessel.vessel_name,
                    title=incident.title,
                    incident_type=type_label,
                    severity=severity,
                    occurred_at=incident.occurred_at.strftime("%d %b %Y, %H:%M UTC"),
                    reported_by=reporter.full_name,
                    location=incident.location or "",
                    operation_number=operation.operation_number if operation else "",
                    incident_url=f"{settings.FRONTEND_URL.rstrip('/')}{path}",
                )
            except Exception as exc:
                logger.warning("vessel incident: email failed for %s: %s", user.email, exc)

    @staticmethod
    async def update_incident(
        incident_id: UUID,
        data: VesselIncidentUpdate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        incident = await VesselRecordService._get_incident(incident_id, db)
        if incident.status == "closed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A closed incident cannot be edited. Reopen it first.",
            )
        update = data.model_dump(exclude_unset=True, exclude={"reason"})
        _refuse_blanked(update, _INCIDENT_REQUIRED)
        changes = capture_diff(incident, update)
        if not changes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing was changed")
        incident.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "EDIT_VESSEL_INCIDENT", "vessel_incident", incident.id,
            changes, request_meta, operation_id=incident.operation_id, reason=data.reason,
        ))
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def investigate_incident(
        incident_id: UUID,
        data: VesselIncidentInvestigate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        incident = await VesselRecordService._get_incident(incident_id, db)
        if incident.status != "open":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only an open incident can be moved to investigation",
            )
        incident.status = "under_investigation"
        incident.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "INVESTIGATE_VESSEL_INCIDENT", "vessel_incident", incident.id,
            {"status": {"from": "open", "to": "under_investigation"}},
            request_meta, operation_id=incident.operation_id, reason=data.note,
        ))
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def close_incident(
        incident_id: UUID,
        data: VesselIncidentClose,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        incident = await VesselRecordService._get_incident(incident_id, db)
        if incident.status == "closed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incident is already closed")
        previous = incident.status
        incident.status = "closed"
        incident.root_cause = data.root_cause
        incident.corrective_action = data.corrective_action
        incident.closed_by = current_user.id
        incident.closed_at = datetime.utcnow()
        incident.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "CLOSE_VESSEL_INCIDENT", "vessel_incident", incident.id,
            {
                "status": {"from": previous, "to": "closed"},
                "root_cause": data.root_cause,
                "corrective_action": data.corrective_action,
            },
            request_meta, operation_id=incident.operation_id,
        ))
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def reopen_incident(
        incident_id: UUID,
        data: VesselIncidentReopen,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Back to investigation. The old root cause and fix stay on the audit
        log; they are cleared on the record so the next close states its own."""
        incident = await VesselRecordService._get_incident(incident_id, db)
        if incident.status != "closed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a closed incident can be reopened")
        db.add(_audit(
            current_user, "REOPEN_VESSEL_INCIDENT", "vessel_incident", incident.id,
            {
                "status": {"from": "closed", "to": "under_investigation"},
                "previous_root_cause": incident.root_cause,
                "previous_corrective_action": incident.corrective_action,
                "previously_closed_by": str(incident.closed_by) if incident.closed_by else None,
                "previously_closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
            },
            request_meta, operation_id=incident.operation_id, reason=data.reason,
        ))
        incident.status = "under_investigation"
        incident.root_cause = None
        incident.corrective_action = None
        incident.closed_by = None
        incident.closed_at = None
        incident.updated_at = datetime.utcnow()
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def add_incident_attachment(
        incident_id: UUID,
        file: UploadFile,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        incident = await VesselRecordService._get_incident(incident_id, db)
        item = await _store_attachment(file, f"{incident.vessel_id}/incidents/{incident.id}", current_user)
        # A new list, not an in-place append — JSONB changes are only seen on reassignment.
        incident.attachments = [*(incident.attachments or []), item]
        incident.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "ATTACH_VESSEL_INCIDENT_FILE", "vessel_incident", incident.id,
            {"file_name": item["file_name"]}, request_meta, operation_id=incident.operation_id,
        ))
        return await VesselRecordService._reload_incident(incident, db)

    @staticmethod
    async def incident_attachment_link(incident_id: UUID, attachment_id: str, db: AsyncSession) -> dict:
        incident = await VesselRecordService._get_incident(incident_id, db)
        return await _attachment_link(incident.attachments, attachment_id)

    # ── Maintenance ────────────────────────────────────────────────────────────

    @staticmethod
    def _maintenance_out(m: VesselMaintenance) -> Dict[str, Any]:
        """Requires .creator eager-loaded."""
        return {
            "id": str(m.id),
            "vessel_id": str(m.vessel_id),
            "maintenance_type": m.maintenance_type,
            "title": m.title,
            "description": m.description,
            "status": m.status,
            "planned_date": m.planned_date.isoformat() if m.planned_date else None,
            "completed_date": m.completed_date.isoformat() if m.completed_date else None,
            "next_due_date": m.next_due_date.isoformat() if m.next_due_date else None,
            "performed_by": m.performed_by,
            "cost": str(m.cost) if m.cost is not None else None,
            "cost_currency": m.cost_currency,
            "attachments": _attachments_out(m.attachments),
            "created_by": str(m.created_by),
            "created_by_name": m.creator.full_name if m.creator else None,
            "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }

    @staticmethod
    async def _get_maintenance(record_id: UUID, db: AsyncSession) -> VesselMaintenance:
        result = await db.execute(
            select(VesselMaintenance)
            .options(selectinload(VesselMaintenance.creator))
            .where(VesselMaintenance.id == record_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Maintenance record not found")
        return record

    @staticmethod
    async def _reload_maintenance(record: VesselMaintenance, db: AsyncSession) -> Dict[str, Any]:
        await db.flush()
        await db.refresh(record, attribute_names=["creator"])
        return VesselRecordService._maintenance_out(record)

    @staticmethod
    def _check_dates(record: VesselMaintenance) -> None:
        if record.status == "done" and not record.completed_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Give the date the work was done")
        if record.completed_date and record.completed_date > date.today():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The date done cannot be in the future")

    @staticmethod
    async def list_maintenance(vessel_id: UUID, db: AsyncSession) -> Dict[str, Any]:
        await _vessel_or_404(vessel_id, db)
        result = await db.execute(
            select(VesselMaintenance)
            .options(selectinload(VesselMaintenance.creator))
            .where(VesselMaintenance.vessel_id == vessel_id)
            .order_by(VesselMaintenance.created_at.desc())
        )
        records = list(result.scalars().all())
        return {
            "records": [VesselRecordService._maintenance_out(m) for m in records],
            "due": due_items(records, date.today()),
        }

    @staticmethod
    async def create_maintenance(
        vessel_id: UUID,
        data: VesselMaintenanceCreate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        vessel = await _vessel_or_404(vessel_id, db)
        record = VesselMaintenance(
            vessel_id=vessel.id,
            created_by=current_user.id,
            attachments=[],
            **data.model_dump(),
        )
        VesselRecordService._check_dates(record)
        db.add(record)
        await db.flush()
        db.add(_audit(
            current_user, "LOG_VESSEL_MAINTENANCE", "vessel_maintenance", record.id,
            {
                "vessel_name": vessel.vessel_name,
                "maintenance_type": data.maintenance_type,
                "title": data.title,
                "status": data.status,
            },
            request_meta,
        ))
        return await VesselRecordService._reload_maintenance(record, db)

    @staticmethod
    async def update_maintenance(
        record_id: UUID,
        data: VesselMaintenanceUpdate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        record = await VesselRecordService._get_maintenance(record_id, db)
        update = data.model_dump(exclude_unset=True, exclude={"reason"})
        _refuse_blanked(update, _MAINTENANCE_REQUIRED)
        changes = capture_diff(record, update)
        if not changes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing was changed")
        VesselRecordService._check_dates(record)
        record.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "EDIT_VESSEL_MAINTENANCE", "vessel_maintenance", record.id,
            changes, request_meta, reason=data.reason,
        ))
        return await VesselRecordService._reload_maintenance(record, db)

    @staticmethod
    async def add_maintenance_attachment(
        record_id: UUID,
        file: UploadFile,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        record = await VesselRecordService._get_maintenance(record_id, db)
        item = await _store_attachment(file, f"{record.vessel_id}/maintenance/{record.id}", current_user)
        record.attachments = [*(record.attachments or []), item]
        record.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "ATTACH_VESSEL_MAINTENANCE_FILE", "vessel_maintenance", record.id,
            {"file_name": item["file_name"]}, request_meta,
        ))
        return await VesselRecordService._reload_maintenance(record, db)

    @staticmethod
    async def maintenance_attachment_link(record_id: UUID, attachment_id: str, db: AsyncSession) -> dict:
        record = await VesselRecordService._get_maintenance(record_id, db)
        return await _attachment_link(record.attachments, attachment_id)
