from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.client_notification import (
    SendClientNotificationRequest, ClientNotificationRecipientOut, ClientNotificationLogOut,
)
from app.services.client_notification_service import ClientNotificationService

router = APIRouter(tags=["Client Notifications"])

_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.get("/operations/{operation_id}/client-notifications/recipients", response_model=StandardResponse)
async def list_recipients(
    operation_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Every client-vessel reachable through this operation's linked Naval
    Clearance — the tick-to-send screen's source list. Empty if no
    clearance is linked. The frontend must default every row unticked."""
    recipients = await ClientNotificationService.list_eligible_recipients(operation_id, db)
    return StandardResponse.ok(data=[ClientNotificationRecipientOut(**r).model_dump() for r in recipients])


@router.post("/operations/{operation_id}/client-notifications/send", response_model=StandardResponse)
async def send_notification(
    operation_id: UUID,
    body: SendClientNotificationRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Only the Bunker Manager sends client notifications. Every recipient
    is re-verified server-side against this operation's clearance — never
    trust the client-supplied list at face value. One isolated email per
    recipient, one log row per recipient."""
    logs = await ClientNotificationService.send_client_notification(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=[ClientNotificationLogOut.model_validate(l).model_dump() for l in logs],
        message=f"Notification sent to {len(logs)} recipient(s)",
    )


@router.get("/operations/{operation_id}/client-notifications/log", response_model=StandardResponse)
async def get_notification_log(
    operation_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    logs = await ClientNotificationService.get_notification_log(operation_id, db)
    return StandardResponse.ok(data=[ClientNotificationLogOut.model_validate(l).model_dump() for l in logs])
