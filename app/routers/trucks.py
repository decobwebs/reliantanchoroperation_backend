from typing import Optional
from uuid import UUID
import mimetypes
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, UploadFile, File, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import User
from app.models.enums import UserRole, TruckWaiverStatus
from app.schemas.common import StandardResponse
from pydantic import BaseModel
from app.schemas.truck import (
    TruckCreate, TruckUpdate, TruckOut,
    TruckOperationCreate, TruckOperationUpdate, TruckOperationOut,
    TruckFeedbackCreate, TruckFeedbackOut,
    TruckTransitRequest, TruckDischargeEndRequest,
    DischargeApproveRequest, DischargeEditRequest,
    TruckDepartParkingRequest, TruckArrivedLoadingRequest,
    TruckDepartedLoadingRequest, TruckArrivedDischargeRequest,
    TruckEventRequest, TruckCompletionRequest,
    FeedbackApproveRequest, FeedbackRejectRequest,
    TruckProfileOut,
    TruckSafetyAuditCreate, TruckSafetyAuditOut,
    WaiveAuditItemRequest,
    TruckWaiverBulkCreate, TruckWaiverOut, TruckWaybillLinkRequest,
    TruckWaiverUpdate, TruckWaiverDeleteRequest,
)
from app.services.truck_service import TruckService
from app.services.document_service import _upload_to_supabase, MAX_FILE_SIZE_BYTES
from app.config import settings

router = APIRouter(tags=["Trucks"])


# ── Truck Registry ─────────────────────────────────────────────────────────────

@router.get(
    "/trucks",
    response_model=StandardResponse,
)
async def list_trucks(
    active_only: bool = Query(True),
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List trucks. Available to BM and Logistics Officer."""
    trucks = await TruckService.list_trucks(db, active_only=active_only)
    items = [TruckOut.model_validate(t).model_dump() for t in trucks]
    return StandardResponse.ok(data=items, message="Trucks retrieved")


@router.post(
    "/trucks",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_truck(
    body: TruckCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Register a new truck. BM (Fleet Library) or Logistics Officer (inline while sourcing)."""
    truck = await TruckService.create_truck(body, current_user, db)
    return StandardResponse.ok(
        data=TruckOut.model_validate(truck).model_dump(),
        message=f"Truck {truck.truck_number} created",
    )


# ── Waivers (regulatory / BFL truck numbers) ────────────────────────────────────
# NOTE: these two static paths (/trucks/waivers, /trucks/waivers/bulk) MUST be
# registered before the dynamic /trucks/{truck_id} route below — FastAPI/Starlette
# matches routes in registration order, so a later static path here would be
# shadowed by {truck_id} matching "waivers" as a (invalid) UUID path param.

@router.post("/trucks/waivers/bulk", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def bulk_add_waivers(
    body: TruckWaiverBulkCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-add waiver/regulatory truck numbers up front, before sourcing starts. Bunker Manager (admin) only."""
    result = await TruckService.bulk_add_waivers(body.waybill_truck_numbers, current_user, db)
    return StandardResponse.ok(
        data=result,
        message=f"{len(result['created'])} waiver number(s) added"
        + (f", {len(result['skipped_duplicates'])} duplicate(s) skipped" if result["skipped_duplicates"] else ""),
    )


@router.get("/trucks/waivers", response_model=StandardResponse)
async def list_waivers(
    status_filter: Optional[TruckWaiverStatus] = Query(None, alias="status"),
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer, UserRole.ops_supervisor)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List waiver numbers, optionally filtered by status (e.g. ?status=available)."""
    waivers = await TruckService.list_waivers(db, status_filter=status_filter)
    items = [TruckWaiverOut.model_validate(w).model_dump() for w in waivers]
    return StandardResponse.ok(data=items, message="Waiver numbers retrieved")


@router.put("/trucks/waivers/{waiver_id}", response_model=StandardResponse)
async def update_waiver(
    waiver_id: UUID,
    body: TruckWaiverUpdate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Edit a waiver number's value. Requires a reason. Bunker Manager only."""
    waiver = await TruckService.update_waiver(waiver_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckWaiverOut.model_validate(waiver).model_dump(),
        message=f"Waiver number updated to {waiver.waybill_truck_number}",
    )


@router.delete("/trucks/waivers/{waiver_id}", response_model=StandardResponse)
async def delete_waiver(
    waiver_id: UUID,
    body: TruckWaiverDeleteRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Remove a waiver number. Requires a reason. Blocked once linked to a truck. Bunker Manager only."""
    await TruckService.delete_waiver(waiver_id, body.reason, current_user, db)
    return StandardResponse.ok(data=None, message="Waiver number removed")


@router.get(
    "/trucks/{truck_id}",
    response_model=StandardResponse,
)
async def get_truck(
    truck_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer, UserRole.ops_supervisor)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get single truck detail."""
    truck = await TruckService.get_truck(truck_id, db)
    return StandardResponse.ok(data=TruckOut.model_validate(truck).model_dump(), message="Truck retrieved")


@router.get(
    "/trucks/{truck_id}/profile",
    response_model=StandardResponse,
)
async def get_truck_profile(
    truck_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer, UserRole.ops_supervisor)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Full truck profile with stats and operation history."""
    profile = await TruckService.get_truck_profile(truck_id, db)
    truck_out = TruckOut.model_validate(profile["truck"]).model_dump()
    return StandardResponse.ok(
        data={
            "truck": truck_out,
            "stats": profile["stats"],
            "history": profile["history"],
        },
        message="Truck profile retrieved",
    )


@router.put(
    "/trucks/{truck_id}",
    response_model=StandardResponse,
)
async def update_truck(
    truck_id: UUID,
    body: TruckUpdate,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Update truck details. BM or Logistics Officer."""
    truck = await TruckService.update_truck(truck_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckOut.model_validate(truck).model_dump(),
        message="Truck updated",
    )


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
TRUCK_PHOTO_BUCKET = "truck-photos"


@router.post(
    "/trucks/{truck_id}/photo",
    response_model=StandardResponse,
)
async def upload_truck_photo(
    truck_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a truck photo to Supabase storage and update the truck record."""
    content = await file.read()

    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit",
        )

    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, and WebP images are allowed for truck photos",
        )

    safe_name = (file.filename or "photo").replace("..", "").replace("/", "_").replace("\\", "_")
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid4())[:8]
    storage_path = f"{truck_id}/{timestamp}_{unique_id}_{safe_name}"

    photo_url = await _upload_to_supabase(content, storage_path, mime_type, bucket=TRUCK_PHOTO_BUCKET)

    truck = await TruckService.update_truck(
        truck_id, TruckUpdate(photo_url=photo_url), current_user, db
    )
    return StandardResponse.ok(
        data=TruckOut.model_validate(truck).model_dump(),
        message="Truck photo uploaded",
    )


DOC_TYPE_FIELDS = {"licence": "truck_licence_url", "calibration_cert": "calibration_cert_url"}
TRUCK_DOCS_BUCKET = "truck-documents"
ALLOWED_DOC_TYPES = {"application/pdf"}


@router.post(
    "/trucks/{truck_id}/documents/{doc_type}",
    response_model=StandardResponse,
)
async def upload_truck_document(
    truck_id: UUID,
    doc_type: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(UserRole.bunker_manager, UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a truck licence or calibration certificate PDF."""
    if doc_type not in DOC_TYPE_FIELDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document type")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit",
        )

    mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are allowed for truck documents",
        )

    safe_name = (file.filename or doc_type).replace("..", "").replace("/", "_").replace("\\", "_")
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid4())[:8]
    storage_path = f"{truck_id}/{doc_type}/{timestamp}_{unique_id}_{safe_name}"

    doc_url = await _upload_to_supabase(content, storage_path, mime_type, bucket=TRUCK_DOCS_BUCKET)

    truck = await TruckService.update_truck(
        truck_id, TruckUpdate(**{DOC_TYPE_FIELDS[doc_type]: doc_url}), current_user, db
    )
    return StandardResponse.ok(
        data=TruckOut.model_validate(truck).model_dump(),
        message=f"Truck {doc_type.replace('_', ' ')} uploaded",
    )


class TrucksRequiredRequest(BaseModel):
    trucks_required: int


@router.put("/operations/{operation_id}/trucks-required", response_model=StandardResponse)
async def set_trucks_required(
    operation_id: UUID,
    body: TrucksRequiredRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Set how many trucks are required for this operation. Bunker Manager only."""
    operation = await TruckService.set_trucks_required(operation_id, body.trucks_required, current_user, db)
    return StandardResponse.ok(
        data={"trucks_required": operation.trucks_required},
        message="Trucks required updated",
    )


# ── Truck Operations on an Operation ──────────────────────────────────────────

@router.get("/operations/{operation_id}/trucks", response_model=StandardResponse)
async def list_truck_operations(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List truck operations for a given operation."""
    truck_ops = await TruckService.list_truck_operations(operation_id, current_user, db)
    items = [TruckOperationOut.model_validate(t).model_dump() for t in truck_ops]
    return StandardResponse.ok(data=items, message="Truck operations retrieved")


@router.post(
    "/operations/{operation_id}/trucks",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_truck_to_operation(
    operation_id: UUID,
    body: TruckOperationCreate,
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Add a truck to an operation. Logistics Officer only."""
    truck_op = await TruckService.add_truck_to_operation(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Truck added to operation",
    )


@router.put(
    "/operations/{operation_id}/trucks/{truck_op_id}",
    response_model=StandardResponse,
)
async def update_truck_operation(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckOperationUpdate,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.bunker_manager, UserRole.ops_supervisor)),
    db: AsyncSession = Depends(get_db),
):
    """Update a truck operation record."""
    truck_op = await TruckService.update_truck_operation(
        operation_id, truck_op_id, body, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Truck operation updated",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/waybill",
    response_model=StandardResponse,
)
async def link_waybill(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckWaybillLinkRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Link a waiver number + driver to this truck at waybill-generation time. Logistics Officer only."""
    truck_op = await TruckService.link_waybill(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Waybill linked",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/audit",
    response_model=StandardResponse,
)
async def submit_safety_audit(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckSafetyAuditCreate,
    current_user: User = Depends(
        require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Submit or update safety audit for a truck. Must pass before Stage 1 can be recorded."""
    audit = await TruckService.submit_safety_audit(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckSafetyAuditOut.model_validate(audit).model_dump(),
        message="Safety audit submitted",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/audit/waive",
    response_model=StandardResponse,
)
async def waive_audit_item(
    operation_id: UUID,
    truck_op_id: UUID,
    body: WaiveAuditItemRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """BM can waive a specific failed checklist item with a reason."""
    audit = await TruckService.waive_audit_item(
        operation_id, truck_op_id, body.phase, body.item, body.waiver_notes, current_user, db
    )
    return StandardResponse.ok(
        data=TruckSafetyAuditOut.model_validate(audit).model_dump(),
        message="Audit item waived",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/start-transit",
    response_model=StandardResponse,
)
async def start_transit(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckTransitRequest = TruckTransitRequest(),
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Mark truck as in-transit. Logistics Officer only."""
    truck_op = await TruckService.start_transit(
        operation_id, truck_op_id, body.gps_lat, body.gps_lng, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Transit started",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/end-transit",
    response_model=StandardResponse,
)
async def end_transit(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckTransitRequest = TruckTransitRequest(),
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Mark truck as arrived. Logistics Officer only."""
    truck_op = await TruckService.end_transit(
        operation_id, truck_op_id, body.gps_lat, body.gps_lng, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Transit ended — truck arrived",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/start-discharge",
    response_model=StandardResponse,
)
async def start_discharge(
    operation_id: UUID,
    truck_op_id: UUID,
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Mark truck as discharging. Logistics Officer only."""
    truck_op = await TruckService.start_discharge(
        operation_id, truck_op_id, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Discharge started",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/end-discharge",
    response_model=StandardResponse,
)
async def end_discharge(
    operation_id: UUID,
    truck_op_id: UUID,
    body: TruckDischargeEndRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Complete discharge and record quantity. Logistics Officer only."""
    truck_op = await TruckService.end_discharge(
        operation_id, truck_op_id, body, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Discharge completed — awaiting BM approval" if truck_op.discharge_approved is False else "Discharge completed",
    )


@router.post(
    "/operations/{operation_id}/trucks/{truck_op_id}/approve-discharge",
    response_model=StandardResponse,
)
async def approve_discharge(
    operation_id: UUID,
    truck_op_id: UUID,
    body: DischargeApproveRequest = DischargeApproveRequest(),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Approve a truck discharge record. Writes ROB entry on system vessel. Bunker Manager only."""
    truck_op = await TruckService.approve_discharge(
        operation_id, truck_op_id, body.notes, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Discharge approved — vessel ROB updated",
    )


@router.patch(
    "/operations/{operation_id}/trucks/{truck_op_id}/discharge-record",
    response_model=StandardResponse,
)
async def edit_discharge_record(
    operation_id: UUID,
    truck_op_id: UUID,
    body: DischargeEditRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Edit a completed discharge record. Bunker Manager only. Logged in audit trail as BM edit."""
    truck_op = await TruckService.edit_discharge_record(
        operation_id, truck_op_id, body, current_user, db
    )
    return StandardResponse.ok(
        data=TruckOperationOut.model_validate(truck_op).model_dump(),
        message="Discharge record updated — change logged in audit trail",
    )


# ── Feedback ──────────────────────────────────────────────────────────────────

@router.post(
    "/operations/{operation_id}/feedback",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    operation_id: UUID,
    body: TruckFeedbackCreate,
    current_user: User = Depends(require_roles(UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Submit truck readiness feedback. Logistics Officer only."""
    feedback = await TruckService.submit_feedback(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckFeedbackOut.model_validate(feedback).model_dump(),
        message="Feedback submitted",
    )


@router.get("/operations/{operation_id}/feedback", response_model=StandardResponse)
async def list_feedback(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.logistics_officer)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List feedback submissions for an operation."""
    feedbacks = await TruckService.list_feedback(operation_id, current_user, db)
    items = [TruckFeedbackOut.model_validate(f).model_dump() for f in feedbacks]
    return StandardResponse.ok(data=items, message="Feedback list retrieved")


@router.post(
    "/operations/{operation_id}/feedback/{feedback_id}/approve",
    response_model=StandardResponse,
)
async def approve_feedback(
    operation_id: UUID,
    feedback_id: UUID,
    body: FeedbackApproveRequest = FeedbackApproveRequest(),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Approve truck feedback. Bunker Manager only."""
    feedback = await TruckService.approve_feedback(
        operation_id, feedback_id, current_user, db
    )
    return StandardResponse.ok(
        data=TruckFeedbackOut.model_validate(feedback).model_dump(),
        message="Feedback approved",
    )


@router.post(
    "/operations/{operation_id}/feedback/{feedback_id}/reject",
    response_model=StandardResponse,
)
async def reject_feedback(
    operation_id: UUID,
    feedback_id: UUID,
    body: FeedbackRejectRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Reject truck feedback with a reason (min 10 chars). Bunker Manager only."""
    feedback = await TruckService.reject_feedback(
        operation_id, feedback_id, body.reason, current_user, db
    )
    return StandardResponse.ok(
        data=TruckFeedbackOut.model_validate(feedback).model_dump(),
        message="Feedback rejected",
    )


# ── Truck telemetry milestones ─────────────────────────────────────────────────

@router.post("/operations/{operation_id}/trucks/{truck_op_id}/depart-parking", response_model=StandardResponse)
async def truck_depart_parking(
    operation_id: UUID, truck_op_id: UUID,
    body: TruckDepartParkingRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record truck departure from parking/depot."""
    truck_op = await TruckService.record_depart_parking(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(data=TruckOperationOut.model_validate(truck_op).model_dump(), message="Departure from parking recorded")


@router.post("/operations/{operation_id}/trucks/{truck_op_id}/arrived-loading", response_model=StandardResponse)
async def truck_arrived_loading(
    operation_id: UUID, truck_op_id: UUID,
    body: TruckArrivedLoadingRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record truck arrival at loading/collection point."""
    truck_op = await TruckService.record_arrived_loading(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(data=TruckOperationOut.model_validate(truck_op).model_dump(), message="Arrival at loading point recorded")


@router.post("/operations/{operation_id}/trucks/{truck_op_id}/departed-loading", response_model=StandardResponse)
async def truck_departed_loading(
    operation_id: UUID, truck_op_id: UUID,
    body: TruckDepartedLoadingRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record truck departure from loading point (product collected, transit begins)."""
    truck_op = await TruckService.record_departed_loading(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(data=TruckOperationOut.model_validate(truck_op).model_dump(), message="Departure from loading point recorded")


@router.post("/operations/{operation_id}/trucks/{truck_op_id}/arrived-discharge", response_model=StandardResponse)
async def truck_arrived_discharge(
    operation_id: UUID, truck_op_id: UUID,
    body: TruckArrivedDischargeRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record truck arrival at discharge/delivery location."""
    truck_op = await TruckService.record_arrived_discharge(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(data=TruckOperationOut.model_validate(truck_op).model_dump(), message="Arrival at discharge location recorded")


@router.post("/operations/{operation_id}/trucks/{truck_op_id}/add-event", response_model=StandardResponse)
async def truck_add_event(
    operation_id: UUID, truck_op_id: UUID,
    body: TruckEventRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor, UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Record a custom event on a truck operation (delay, inspection, customs, etc.)."""
    truck_op = await TruckService.record_custom_event(operation_id, truck_op_id, body, current_user, db)
    return StandardResponse.ok(data=TruckOperationOut.model_validate(truck_op).model_dump(), message="Event recorded")


@router.post("/operations/{operation_id}/submit-completion", response_model=StandardResponse)
async def submit_operation_completion(
    operation_id: UUID,
    body: TruckCompletionRequest,
    current_user: User = Depends(require_roles(UserRole.logistics_officer, UserRole.ops_supervisor)),
    db: AsyncSession = Depends(get_db),
):
    """
    Supervisor submits a completion report for the operation.
    Transitions operation to pending_completion for BM review.
    """
    from app.services.truck_service import TruckService as TS
    result = await TS.submit_operation_completion(operation_id, body, current_user, db)
    from app.schemas.operation import OperationOut
    return StandardResponse.ok(
        data=OperationOut.model_validate(result).model_dump(),
        message="Completion report submitted — awaiting Bunker Manager review",
    )
