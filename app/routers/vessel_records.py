"""Vessel incident log and maintenance log (Required System Update, item 1).

Who may do what lives in app/permissions.py: VESSEL_RECORD_ROLES read both
logs, report incidents and keep the maintenance log; INCIDENT_CLOSER_ROLES
(BM + Safety Officer) move an incident to investigation, close or reopen it.
"""
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_request_meta, require_roles
from app.models.user import User
from app.permissions import INCIDENT_CLOSER_ROLES, VESSEL_RECORD_ROLES
from app.schemas.common import StandardResponse
from app.schemas.vessel_record import (
    VesselIncidentCreate, VesselIncidentUpdate, VesselIncidentInvestigate,
    VesselIncidentClose, VesselIncidentReopen,
    VesselMaintenanceCreate, VesselMaintenanceUpdate,
)
from app.services.vessel_record_service import VesselRecordService
from app.services.vessel_report_service import VesselReportService

router = APIRouter(tags=["Vessel records"])


# ── Incidents ──────────────────────────────────────────────────────────────────

@router.get("/vessels/{vessel_id}/incidents", response_model=StandardResponse)
async def list_vessel_incidents(
    vessel_id: UUID,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Every incident logged on this vessel, newest first."""
    items = await VesselRecordService.list_incidents(vessel_id, db)
    return StandardResponse.ok(data=items, message="Vessel incidents retrieved")


@router.post(
    "/vessels/{vessel_id}/incidents",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_vessel_incident(
    vessel_id: UUID,
    body: VesselIncidentCreate,
    request: Request,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Log an incident. The BM and Safety Officer are alerted in-app and by email."""
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.create_incident(vessel_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Incident logged")


@router.patch("/vessel-incidents/{incident_id}", response_model=StandardResponse)
async def edit_vessel_incident(
    incident_id: UUID,
    body: VesselIncidentUpdate,
    request: Request,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Correct an incident's details. A reason is required; closed incidents are locked."""
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.update_incident(incident_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Incident updated")


@router.post("/vessel-incidents/{incident_id}/investigate", response_model=StandardResponse)
async def investigate_vessel_incident(
    incident_id: UUID,
    body: VesselIncidentInvestigate,
    request: Request,
    current_user: User = Depends(require_roles(*INCIDENT_CLOSER_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.investigate_incident(incident_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Incident under investigation")


@router.post("/vessel-incidents/{incident_id}/close", response_model=StandardResponse)
async def close_vessel_incident(
    incident_id: UUID,
    body: VesselIncidentClose,
    request: Request,
    current_user: User = Depends(require_roles(*INCIDENT_CLOSER_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Close with a root cause and the corrective action. BM or Safety Officer."""
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.close_incident(incident_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Incident closed")


@router.post("/vessel-incidents/{incident_id}/reopen", response_model=StandardResponse)
async def reopen_vessel_incident(
    incident_id: UUID,
    body: VesselIncidentReopen,
    request: Request,
    current_user: User = Depends(require_roles(*INCIDENT_CLOSER_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.reopen_incident(incident_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Incident reopened")


@router.post("/vessel-incidents/{incident_id}/attachments", response_model=StandardResponse)
async def attach_vessel_incident_file(
    incident_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Add a photo or document. Files are only ever added, never removed."""
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.add_incident_attachment(incident_id, file, current_user, db, meta)
    return StandardResponse.ok(data=item, message="File attached")


@router.get(
    "/vessel-incidents/{incident_id}/attachments/{attachment_id}",
    response_model=StandardResponse,
)
async def open_vessel_incident_file(
    incident_id: UUID,
    attachment_id: str,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """A short-lived link to one attached file."""
    link = await VesselRecordService.incident_attachment_link(incident_id, attachment_id, db)
    return StandardResponse.ok(data=link, message="File link created")


# ── Maintenance ────────────────────────────────────────────────────────────────

@router.get("/vessels/{vessel_id}/maintenance", response_model=StandardResponse)
async def list_vessel_maintenance(
    vessel_id: UUID,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """The maintenance log, plus what is overdue or due in the next 30 days."""
    data = await VesselRecordService.list_maintenance(vessel_id, db)
    return StandardResponse.ok(data=data, message="Vessel maintenance retrieved")


@router.post(
    "/vessels/{vessel_id}/maintenance",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_vessel_maintenance(
    vessel_id: UUID,
    body: VesselMaintenanceCreate,
    request: Request,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.create_maintenance(vessel_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Maintenance logged")


@router.patch("/vessel-maintenance/{record_id}", response_model=StandardResponse)
async def edit_vessel_maintenance(
    record_id: UUID,
    body: VesselMaintenanceUpdate,
    request: Request,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Any change after logging, including marking it in progress or done. A reason is required."""
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.update_maintenance(record_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Maintenance updated")


@router.post("/vessel-maintenance/{record_id}/attachments", response_model=StandardResponse)
async def attach_vessel_maintenance_file(
    record_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await VesselRecordService.add_maintenance_attachment(record_id, file, current_user, db, meta)
    return StandardResponse.ok(data=item, message="File attached")


@router.get(
    "/vessel-maintenance/{record_id}/attachments/{attachment_id}",
    response_model=StandardResponse,
)
async def open_vessel_maintenance_file(
    record_id: UUID,
    attachment_id: str,
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    link = await VesselRecordService.maintenance_attachment_link(record_id, attachment_id, db)
    return StandardResponse.ok(data=link, message="File link created")


# ── Report and list counts ─────────────────────────────────────────────────────

@router.get("/vessels/{vessel_id}/report", response_model=StandardResponse)
async def vessel_report(
    vessel_id: UUID,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """One vessel over a date range (default: the last 30 days): deliveries,
    ROB movements, HSE checks, incidents and maintenance. No money."""
    date_to = date_to or date.today()
    date_from = date_from or (date_to - timedelta(days=29))
    data = await VesselReportService.build_report(vessel_id, date_from, date_to, current_user, db)
    return StandardResponse.ok(data=data, message="Vessel report built")


@router.get("/vessel-records/summary", response_model=StandardResponse)
async def vessel_records_summary(
    current_user: User = Depends(require_roles(*VESSEL_RECORD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Open incidents and overdue / due-soon maintenance per vessel, for the Vessels list."""
    data = await VesselReportService.records_summary(db)
    return StandardResponse.ok(data=data, message="Vessel record counts retrieved")
