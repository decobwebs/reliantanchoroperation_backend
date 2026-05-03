from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update

from app.models.notification import Notification
from app.models.user import User
from app.models.enums import NotificationType, Priority
from fastapi import HTTPException, status

logger = logging.getLogger("raoms")


async def notify(
    db: AsyncSession,
    user_id: UUID,
    type_: str,
    title: str,
    message: str,
    priority: str = "normal",
    operation_id: Optional[UUID] = None,
    action_url: Optional[str] = None,
    channels: Optional[List[str]] = None,
    # WhatsApp template — if provided AND "whatsapp" is in channels,
    # a WhatsApp message is dispatched fire-and-forget.
    wa_template: Optional[str] = None,
    wa_kwargs: Optional[dict] = None,
) -> None:
    """
    Create an in-app notification record and optionally dispatch WhatsApp.

    WhatsApp is sent when ALL of the following are true:
      1. "whatsapp" is in `channels`
      2. `wa_template` is provided
      3. The recipient user has a `phone` field set
      4. TWILIO_* credentials are configured in .env
    """
    active_channels = channels or ["in_app"]

    notif = Notification(
        user_id=user_id,
        type=NotificationType[type_],
        title=title,
        message=message,
        priority=Priority[priority],
        operation_id=operation_id,
        action_url=action_url,
        delivery_channels=active_channels,
    )
    db.add(notif)

    # ── WhatsApp dispatch ──────────────────────────────────────────────────────
    if "whatsapp" in active_channels and wa_template:
        # Fetch user's phone (lightweight select — only phone column)
        result = await db.execute(
            select(User.phone, User.full_name).where(User.id == user_id)
        )
        row = result.first()
        if row and row.phone:
            from app.services.whatsapp_service import dispatch as wa_dispatch
            kwargs = wa_kwargs or {}
            kwargs.setdefault("name", row.full_name)
            kwargs.setdefault("message", message)
            wa_dispatch(row.phone, wa_template, **kwargs)
            logger.info("WhatsApp queued | user=%s | template=%s", user_id, wa_template)
        else:
            logger.debug(
                "WhatsApp skipped | user=%s | no phone on record", user_id
            )


class NotificationService:

    @staticmethod
    async def list_notifications(
        current_user: User,
        is_read: Optional[bool],
        type_filter: Optional[str],
        page: int,
        per_page: int,
        db: AsyncSession,
    ) -> Tuple[List[Notification], int]:
        conditions = [Notification.user_id == current_user.id]

        if is_read is not None:
            conditions.append(Notification.is_read == is_read)

        if type_filter:
            try:
                conditions.append(Notification.type == NotificationType[type_filter])
            except KeyError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid notification type: {type_filter}",
                )

        count_stmt = (
            select(func.count())
            .select_from(Notification)
            .where(and_(*conditions))
        )
        count_result = await db.execute(count_stmt)
        total = count_result.scalar_one()

        offset = (page - 1) * per_page
        stmt = (
            select(Notification)
            .where(and_(*conditions))
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(per_page)
        )
        result = await db.execute(stmt)
        notifications = list(result.scalars().all())

        return notifications, total

    @staticmethod
    async def mark_read(
        notification_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Notification:
        result = await db.execute(
            select(Notification).where(
                and_(
                    Notification.id == notification_id,
                    Notification.user_id == current_user.id,
                )
            )
        )
        notif = result.scalar_one_or_none()
        if not notif:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found",
            )

        notif.is_read = True
        notif.read_at = datetime.utcnow()
        await db.flush()
        await db.refresh(notif)
        return notif

    @staticmethod
    async def mark_all_read(
        current_user: User,
        db: AsyncSession,
    ) -> int:
        stmt = (
            select(Notification)
            .where(
                and_(
                    Notification.user_id == current_user.id,
                    Notification.is_read == False,
                )
            )
        )
        result = await db.execute(stmt)
        notifications = result.scalars().all()
        now = datetime.utcnow()
        count = 0
        for notif in notifications:
            notif.is_read = True
            notif.read_at = now
            count += 1
        await db.flush()
        return count

    @staticmethod
    async def get_unread_count(
        current_user: User,
        db: AsyncSession,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                and_(
                    Notification.user_id == current_user.id,
                    Notification.is_read == False,
                )
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one()
