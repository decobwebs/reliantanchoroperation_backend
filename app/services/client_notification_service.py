from typing import List, Optional
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.operation import Operation
from app.models.licence import NavalClearance, NavalClearanceVessel
from app.models.notification_log import ClientNotificationLog
from app.models.audit import AuditLog
from app.models.user import User
from app.services.eta_service import EtaService
from app.services.email_service import email_client_notification
from app.schemas.client_notification import SendClientNotificationRequest


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _get_clearance_vessels(operation: Operation, db: AsyncSession) -> List[NavalClearanceVessel]:
    """Every client-vessel reachable through the operation's linked
    clearance — empty if none linked, consistent with the link being
    optional and never a gate."""
    if not operation.naval_clearance_id:
        return []
    result = await db.execute(
        select(NavalClearanceVessel)
        .options(selectinload(NavalClearanceVessel.client))
        .where(NavalClearanceVessel.naval_clearance_id == operation.naval_clearance_id)
    )
    return list(result.scalars().all())


class ClientNotificationService:

    @staticmethod
    async def list_eligible_recipients(operation_id: UUID, db: AsyncSession) -> List[dict]:
        operation = await _get_operation_or_404(operation_id, db)
        vessels = await _get_clearance_vessels(operation, db)
        recipients = []
        for v in vessels:
            eta = await EtaService.get_current_eta(v.id, db)
            recipients.append({
                "naval_clearance_vessel_id": v.id,
                "client_id": v.client_id,
                "client_name": v.client.full_name if v.client else None,
                "client_email": v.client.email if v.client else None,
                "vessel_name": v.vessel_name,
                "imo_number": v.imo_number,
                "current_eta": eta.eta_at if eta else None,
                "previous_eta": None,
            })
        return recipients

    @staticmethod
    def _render_content(
        operation: Operation, vessel: NavalClearanceVessel, notification_type: str,
        eta_at: Optional[datetime], custom_message: Optional[str],
    ) -> tuple[str, str]:
        """Isolated, single-recipient content — only this vessel's own
        details, never anything about another client on the same clearance."""
        if notification_type == "eta_change":
            subject = f"Updated ETA — {vessel.vessel_name} ({operation.operation_number})"
            body = f"The estimated time of arrival for your vessel <strong>{vessel.vessel_name}</strong> has been updated" + (
                f" to <strong>{eta_at.strftime('%d %b %Y, %H:%M')} UTC</strong>." if eta_at else "."
            )
        elif notification_type == "completion":
            subject = f"Delivery Completed — {vessel.vessel_name} ({operation.operation_number})"
            body = f"Delivery to <strong>{vessel.vessel_name}</strong> for operation {operation.operation_number} is complete."
        elif notification_type == "stage_update":
            subject = f"Delivery Update — {vessel.vessel_name} ({operation.operation_number})"
            body = custom_message or f"There is an update on the delivery to <strong>{vessel.vessel_name}</strong>."
        else:
            subject = f"Update — {vessel.vessel_name} ({operation.operation_number})"
            body = custom_message or f"There is an update regarding <strong>{vessel.vessel_name}</strong>."

        if custom_message and notification_type != "stage_update":
            body += f"<br/><br/>{custom_message}"

        return subject, body

    @staticmethod
    async def send_client_notification(
        operation_id: UUID, data: SendClientNotificationRequest, current_user: User, db: AsyncSession,
    ) -> List[ClientNotificationLog]:
        operation = await _get_operation_or_404(operation_id, db)
        vessels = await _get_clearance_vessels(operation, db)
        vessels_by_id = {v.id: v for v in vessels}

        # Re-verify server-side — never trust a client-supplied recipient
        # list at face value, defends against a stale/tampered payload.
        for rid in data.recipient_naval_clearance_vessel_ids:
            if rid not in vessels_by_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Recipient {rid} does not belong to this operation's Naval Clearance",
                )

        sent_logs = []
        for rid in data.recipient_naval_clearance_vessel_ids:
            vessel = vessels_by_id[rid]
            if not vessel.client:
                continue
            eta = await EtaService.get_current_eta(rid, db)
            subject, body = ClientNotificationService._render_content(
                operation, vessel, data.notification_type, eta.eta_at if eta else None, data.custom_message,
            )

            await email_client_notification(
                to_email=vessel.client.email, recipient_name=vessel.client.full_name,
                subject=subject, body_html=body,
            )

            log = ClientNotificationLog(
                operation_id=operation.id,
                naval_clearance_vessel_id=rid,
                client_id=vessel.client_id,
                recipient_email=vessel.client.email,
                recipient_name=vessel.client.full_name,
                notification_type=data.notification_type,
                stage=data.stage,
                subject=subject,
                body_snapshot=body,
                sent_by=current_user.id,
                thread_key=str(operation.id),
            )
            db.add(log)
            sent_logs.append(log)

        if sent_logs:
            db.add(AuditLog(
                user_id=current_user.id, operation_id=operation.id, action="SEND_CLIENT_NOTIFICATION",
                entity_type="client_notification", entity_id=operation.id,
                changes={
                    "notification_type": data.notification_type,
                    "recipient_count": len(sent_logs),
                    "recipient_client_ids": [str(log.client_id) for log in sent_logs],
                },
            ))

        await db.flush()
        for log in sent_logs:
            await db.refresh(log)
        return sent_logs

    @staticmethod
    async def get_notification_log(operation_id: UUID, db: AsyncSession) -> List[ClientNotificationLog]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(ClientNotificationLog)
            .where(ClientNotificationLog.operation_id == operation_id)
            .order_by(ClientNotificationLog.sent_at.desc())
        )
        return list(result.scalars().all())
