"""Inspections and the safety dashboard (Required System Update, item 2).

Who may do what lives in app/permissions.py: INSPECTION_ROLES (BM + Safety
Officer) log inspections and sign off their actions; INSPECTION_VIEWER_ROLES
add the Ops Supervisor as a reader; SAFETY_DASHBOARD_ROLES see the overview.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_request_meta, require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.permissions import INSPECTION_ROLES, INSPECTION_VIEWER_ROLES, SAFETY_DASHBOARD_ROLES
from app.schemas.common import StandardResponse
from app.schemas.inspection import (
    InspectionCreate, InspectionUpdate, InspectionActionCreate, InspectionActionUpdate,
    InspectionActionComplete, InspectionActionReopen,
)
from app.services.inspection_service import InspectionService
from app.services.safety_service import SafetyService

router = APIRouter(tags=["Safety"])

_keepers = Depends(require_roles(*INSPECTION_ROLES))
_viewers = Depends(require_roles(*INSPECTION_VIEWER_ROLES))


@router.get("/safety/overview", response_model=StandardResponse)
async def safety_overview(
    current_user: User = Depends(require_roles(*SAFETY_DASHBOARD_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Fleet-wide: open incidents, failed checks (last 30 days), trucks missing
    a Pre or Post audit on live operations, open truck issues, maintenance
    due, and inspection actions still open."""
    data = await SafetyService.overview(db)
    return StandardResponse.ok(data=data, message="Safety overview built")


# ── Inspections ────────────────────────────────────────────────────────────────

@router.get("/inspections/owners", response_model=StandardResponse)
async def list_action_owners(
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Active staff who can be given a follow-up action: name and role only."""
    rows = (await db.execute(
        select(User.id, User.full_name, User.role)
        .where(and_(User.is_active == True, User.role != UserRole.client))  # noqa: E712
        .order_by(User.full_name)
    )).all()
    data = [{"id": str(r.id), "full_name": r.full_name, "role": r.role.value} for r in rows]
    return StandardResponse.ok(data=data, message="Staff retrieved")


@router.get("/inspections", response_model=StandardResponse)
async def list_inspections(
    subject_type: Optional[str] = Query(None),
    vessel_id: Optional[UUID] = Query(None),
    truck_id: Optional[UUID] = Query(None),
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    """Inspections, newest first, with their actions."""
    items = await InspectionService.list_inspections(db, subject_type, vessel_id, truck_id)
    return StandardResponse.ok(data=items, message="Inspections retrieved")


@router.post("/inspections", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def log_inspection(
    body: InspectionCreate,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Log an inspection. Each action's owner is told in-app."""
    meta = get_request_meta(request, current_user)
    item = await InspectionService.create_inspection(body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Inspection logged")


@router.get("/inspections/{inspection_id}", response_model=StandardResponse)
async def get_inspection(
    inspection_id: UUID,
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    item = await InspectionService.get_inspection(inspection_id, db)
    return StandardResponse.ok(data=item, message="Inspection retrieved")


@router.patch("/inspections/{inspection_id}", response_model=StandardResponse)
async def edit_inspection(
    inspection_id: UUID,
    body: InspectionUpdate,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Correct an inspection. A reason is required and kept on the audit log."""
    meta = get_request_meta(request, current_user)
    item = await InspectionService.update_inspection(inspection_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Inspection updated")


@router.post("/inspections/{inspection_id}/actions", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def add_inspection_action(
    inspection_id: UUID,
    body: InspectionActionCreate,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await InspectionService.add_action(inspection_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Action added")


@router.post("/inspections/{inspection_id}/attachments", response_model=StandardResponse)
async def add_inspection_file(
    inspection_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Add a photo or document. Files are only ever added, never removed."""
    meta = get_request_meta(request, current_user)
    item = await InspectionService.add_attachment(inspection_id, file, current_user, db, meta)
    return StandardResponse.ok(data=item, message="File added")


@router.get("/inspections/{inspection_id}/attachments/{attachment_id}", response_model=StandardResponse)
async def open_inspection_file(
    inspection_id: UUID,
    attachment_id: str,
    current_user: User = _viewers,
    db: AsyncSession = Depends(get_db),
):
    link = await InspectionService.attachment_link(inspection_id, attachment_id, db)
    return StandardResponse.ok(data=link, message="File link created")


# ── Follow-up actions ──────────────────────────────────────────────────────────

@router.patch("/inspection-actions/{action_id}", response_model=StandardResponse)
async def edit_inspection_action(
    action_id: UUID,
    body: InspectionActionUpdate,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Change what, who or by when. A reason is required."""
    meta = get_request_meta(request, current_user)
    item = await InspectionService.update_action(action_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Action updated")


@router.post("/inspection-actions/{action_id}/complete", response_model=StandardResponse)
async def complete_inspection_action(
    action_id: UUID,
    body: InspectionActionComplete,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    """Mark the fix done, with a note on what was done."""
    meta = get_request_meta(request, current_user)
    item = await InspectionService.complete_action(action_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Action marked done")


@router.post("/inspection-actions/{action_id}/reopen", response_model=StandardResponse)
async def reopen_inspection_action(
    action_id: UUID,
    body: InspectionActionReopen,
    request: Request,
    current_user: User = _keepers,
    db: AsyncSession = Depends(get_db),
):
    meta = get_request_meta(request, current_user)
    item = await InspectionService.reopen_action(action_id, body, current_user, db, meta)
    return StandardResponse.ok(data=item, message="Action reopened")
