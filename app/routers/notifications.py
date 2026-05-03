from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.notification import NotificationOut, UnreadCountOut
from app.services.notification_service import NotificationService
from app.services.whatsapp_service import dispatch as wa_dispatch, _is_configured, TEMPLATES

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=PaginatedResponse)
async def list_notifications(
    is_read: Optional[bool] = Query(None),
    type_filter: Optional[str] = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the current user."""
    notifications, total = await NotificationService.list_notifications(
        current_user, is_read, type_filter, page, per_page, db
    )
    items = [NotificationOut.model_validate(n).model_dump() for n in notifications]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.put("/{notification_id}/read", response_model=StandardResponse)
async def mark_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a notification as read."""
    notif = await NotificationService.mark_read(notification_id, current_user, db)
    return StandardResponse.ok(
        data=NotificationOut.model_validate(notif).model_dump(),
        message="Notification marked as read",
    )


@router.put("/read-all", response_model=StandardResponse)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read for the current user."""
    count = await NotificationService.mark_all_read(current_user, db)
    return StandardResponse.ok(
        data={"marked_count": count},
        message=f"{count} notification(s) marked as read",
    )


@router.get("/unread-count", response_model=StandardResponse)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get count of unread notifications for the current user."""
    count = await NotificationService.get_unread_count(current_user, db)
    return StandardResponse.ok(
        data=UnreadCountOut(unread_count=count).model_dump(),
        message="Unread count retrieved",
    )


# ── WhatsApp ──────────────────────────────────────────────────────────────────

class WhatsAppTestRequest(BaseModel):
    phone: str
    template: str = "generic"
    message: str = "This is a test notification from RAOMS."


@router.post("/whatsapp/test", response_model=StandardResponse)
async def test_whatsapp(
    body: WhatsAppTestRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Send a test WhatsApp message to any phone number.
    BM-only. Used to verify Twilio credentials and sandbox setup.
    """
    if current_user.role != UserRole.bunker_manager:
        raise HTTPException(status_code=403, detail="BM access only")

    if not _is_configured():
        return StandardResponse.ok(
            data={"sent": False, "reason": "Twilio credentials not configured in .env"},
            message="WhatsApp not configured",
        )

    if body.template not in TEMPLATES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown template '{body.template}'. Available: {list(TEMPLATES.keys())}",
        )

    wa_dispatch(
        body.phone,
        body.template,
        name=current_user.full_name,
        message=body.message,
        # Safe defaults for template vars that may not be provided
        operation_number="TEST-0000",
        task_type="Test Task",
        priority="NORMAL",
        bdn_number="BDN-TEST",
        quantity="0",
        pfi_number="PFI-TEST",
        amount="0",
        currency="USD",
        reference="TEST-REF",
        invoice_number="INV-TEST",
        vessel_name="Test Vessel",
        current_rob="0",
        threshold="0",
        new_status="test_status",
        extra="",
        op_type="Test",
        client="Test Client",
        reason="Test reason",
    )

    return StandardResponse.ok(
        data={"sent": True, "to": body.phone, "template": body.template},
        message=f"WhatsApp message dispatched to {body.phone}",
    )


@router.get("/whatsapp/status", response_model=StandardResponse)
async def whatsapp_status(
    current_user: User = Depends(get_current_user),
):
    """Check WhatsApp / Twilio configuration status."""
    from app.config import settings
    configured = _is_configured()
    return StandardResponse.ok(
        data={
            "configured": configured,
            "from_number": settings.TWILIO_WHATSAPP_FROM if configured else None,
            "account_sid_set": bool(settings.TWILIO_ACCOUNT_SID),
            "auth_token_set": bool(settings.TWILIO_AUTH_TOKEN),
            "available_templates": list(TEMPLATES.keys()),
        },
        message="WhatsApp status" if configured else "WhatsApp not configured — add TWILIO_* to .env",
    )
