from typing import List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.operation import Operation
from app.models.notification_log import OperationNotification, OperationNotificationRecipient
from app.models.user import User
from app.models.audit import AuditLog
from app.models.enums import UserRole
from app.services.notification_service import notify
from app.schemas.operation_notification import SendOperationNotificationRequest

# A wholly separate channel from _notify_all_finance / _notify_assigned_users
# in operation_service.py — shares nothing with them but the notify() helper.


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


class OperationNotificationService:

    @staticmethod
    async def list_eligible_staff(db: AsyncSession) -> List[User]:
        """Every active non-client user — the BM's recipient picker source."""
        result = await db.execute(
            select(User).where(and_(User.is_active == True, User.role != UserRole.client))
        )
        return list(result.scalars().all())

    @staticmethod
    async def send_operation_notification(
        operation_id: UUID, data: SendOperationNotificationRequest, current_user: User, db: AsyncSession,
    ) -> OperationNotification:
        operation = await _get_operation_or_404(operation_id, db)

        if data.all_staff:
            recipients = await OperationNotificationService.list_eligible_staff(db)
        else:
            if not data.recipient_user_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Select at least one recipient, or send to all active staff",
                )
            # Re-verify server-side — never trust a client-supplied recipient
            # list at face value.
            result = await db.execute(
                select(User).where(
                    and_(User.id.in_(data.recipient_user_ids), User.is_active == True, User.role != UserRole.client)
                )
            )
            recipients = list(result.scalars().all())
            if not recipients:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No valid recipients found")

        log = OperationNotification(
            operation_id=operation.id, sent_by=current_user.id,
            title=data.title, message=data.message,
        )
        db.add(log)
        await db.flush()

        for recipient in recipients:
            await notify(
                db=db, user_id=recipient.id, type_="system",
                title=data.title, message=data.message,
                priority="normal", operation_id=operation.id,
                action_url=f"/operations/{operation.id}",
            )
            db.add(OperationNotificationRecipient(
                operation_notification_id=log.id, user_id=recipient.id,
            ))

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="SEND_OPERATION_NOTIFICATION",
            entity_type="operation_notification", entity_id=log.id,
            changes={"title": data.title, "recipient_count": len(recipients), "all_staff": data.all_staff},
        ))

        await db.flush()
        await db.refresh(log, attribute_names=["recipients"])
        log._sender_name = current_user.full_name
        names_by_user_id = {r.id: r.full_name for r in recipients}
        for r in log.recipients:
            r._user_name = names_by_user_id.get(r.user_id)
        return log

    @staticmethod
    async def get_notification_log(operation_id: UUID, db: AsyncSession) -> List[OperationNotification]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(OperationNotification)
            .where(OperationNotification.operation_id == operation_id)
            .options(selectinload(OperationNotification.recipients).selectinload(OperationNotificationRecipient.user), selectinload(OperationNotification.sender))
            .order_by(OperationNotification.sent_at.desc())
        )
        logs = list(result.scalars().all())
        for log in logs:
            log._sender_name = log.sender.full_name if log.sender else None
            for r in log.recipients:
                r._user_name = r.user.full_name if r.user else None
        return logs
