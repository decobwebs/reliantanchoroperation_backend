from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles, require_operation_manager
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.operation_notification import (
    SendOperationNotificationRequest, OperationNotificationOut, OperationNotificationRecipientOut, StaffRecipientOut,
)
from app.services.operation_notification_service import OperationNotificationService

router = APIRouter(tags=["Operation Notifications"])

_op_manager = Depends(require_operation_manager())


@router.get("/operation-notifications/staff", response_model=StandardResponse)
async def list_eligible_staff(
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Every active non-client user — the recipient picker's source list."""
    staff = await OperationNotificationService.list_eligible_staff(db)
    return StandardResponse.ok(
        data=[StaffRecipientOut(id=u.id, full_name=u.full_name, role=u.role.value).model_dump() for u in staff]
    )


@router.post("/operations/{operation_id}/notifications", response_model=StandardResponse)
async def send_operation_notification(
    operation_id: UUID,
    body: SendOperationNotificationRequest,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    """Only the Bunker Manager sends General notifications — a wholly
    separate stream from the automatic role-scoped notifications."""
    log = await OperationNotificationService.send_operation_notification(operation_id, body, current_user, db)
    data = OperationNotificationOut.model_validate(log).model_dump()
    data["sent_by_name"] = getattr(log, "_sender_name", None)
    data["recipients"] = [
        {**OperationNotificationRecipientOut.model_validate(r).model_dump(), "user_name": getattr(r, "_user_name", None)}
        for r in log.recipients
    ]
    return StandardResponse.ok(data=data, message=f"Notification sent to {len(log.recipients)} recipient(s)")


@router.get("/operations/{operation_id}/notifications", response_model=StandardResponse)
async def get_operation_notification_log(
    operation_id: UUID,
    current_user: User = _op_manager,
    db: AsyncSession = Depends(get_db),
):
    logs = await OperationNotificationService.get_notification_log(operation_id, db)
    items = []
    for log in logs:
        item = OperationNotificationOut.model_validate(log).model_dump()
        item["sent_by_name"] = getattr(log, "_sender_name", None)
        item["recipients"] = [
            {**OperationNotificationRecipientOut.model_validate(r).model_dump(), "user_name": getattr(r, "_user_name", None)}
            for r in log.recipients
        ]
        items.append(item)
    return StandardResponse.ok(data=items)
