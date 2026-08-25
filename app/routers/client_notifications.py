from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles, require_operation_manager
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.client_notification import (
    QueueClientNotificationRequest, ApprovePendingNotificationsRequest, SendApprovedNotificationsRequest,
    RejectPendingNotificationRequest, ClientNotificationRecipientOut, PendingClientNotificationOut, ClientNotificationLogOut,
)
from app.services.client_notification_service import ClientNotificationService

router = APIRouter(tags=["Client Notifications"])

_op_manager = Depends(require_operation_manager())


@router.get("/operations/{operation_id}/client-notifications/recipients", response_model=StandardResponse)
async def list_recipients(
    operation_id: UUID,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Every eligible recipient for this operation — Naval Clearance vessels
    plus Cast Off client emails, deduplicated. The tick-to-queue screen's
    source list. The frontend must default every row unticked."""
    recipients = await ClientNotificationService.list_eligible_recipients(operation_id, db)
    return StandardResponse.ok(data=[ClientNotificationRecipientOut(**r).model_dump() for r in recipients])


@router.post("/operations/{operation_id}/client-notifications/queue", response_model=StandardResponse)
async def queue_notification(
    operation_id: UUID,
    body: QueueClientNotificationRequest,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Queues recipients for approval — nothing is emailed yet. Every
    recipient is re-verified server-side, never trusted from the payload."""
    pending = await ClientNotificationService.queue_client_notification(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=[PendingClientNotificationOut.model_validate(p).model_dump() for p in pending],
        message=f"{len(pending)} recipient(s) queued for approval",
    )


@router.post("/operations/{operation_id}/client-notifications/approve", response_model=StandardResponse)
async def approve_notifications(
    operation_id: UUID,
    body: ApprovePendingNotificationsRequest,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Approves queued recipients — still nothing is emailed until Send is
    pressed separately."""
    approved = await ClientNotificationService.approve_pending_notifications(operation_id, body.pending_ids, current_user, db)
    return StandardResponse.ok(
        data=[PendingClientNotificationOut.model_validate(p).model_dump() for p in approved],
        message=f"{len(approved)} recipient(s) approved",
    )


@router.post("/operations/{operation_id}/client-notifications/send-approved", response_model=StandardResponse)
async def send_approved_notifications(
    operation_id: UUID,
    body: SendApprovedNotificationsRequest,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Sends approved recipients — one isolated email per recipient, one
    log row per recipient, exactly as the old direct-send endpoint did."""
    logs = await ClientNotificationService.send_approved_notifications(operation_id, body.pending_ids, current_user, db)
    return StandardResponse.ok(
        data=[ClientNotificationLogOut.model_validate(l).model_dump() for l in logs],
        message=f"Sent to {len(logs)} recipient(s)",
    )


@router.post("/operations/{operation_id}/client-notifications/{pending_id}/reject", response_model=StandardResponse)
async def reject_pending_notification(
    operation_id: UUID,
    pending_id: UUID,
    body: RejectPendingNotificationRequest,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Drops a queued or approved recipient — a wrong tick, corrected before it's ever sent."""
    row = await ClientNotificationService.reject_pending_notification(operation_id, pending_id, body.reason, current_user, db)
    return StandardResponse.ok(data=PendingClientNotificationOut.model_validate(row).model_dump(), message="Removed from queue")


@router.get("/operations/{operation_id}/client-notifications/pending", response_model=StandardResponse)
async def list_pending_notifications(
    operation_id: UUID,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Everything awaiting approval or approved but not yet sent."""
    pending = await ClientNotificationService.get_pending_notifications(operation_id, db)
    return StandardResponse.ok(data=[PendingClientNotificationOut.model_validate(p).model_dump() for p in pending])


@router.get("/operations/{operation_id}/client-notifications/log", response_model=StandardResponse)
async def get_notification_log(
    operation_id: UUID,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    logs = await ClientNotificationService.get_notification_log(operation_id, db)
    return StandardResponse.ok(data=[ClientNotificationLogOut.model_validate(l).model_dump() for l in logs])
