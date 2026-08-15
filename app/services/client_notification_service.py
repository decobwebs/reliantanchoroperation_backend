from typing import List, Optional
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.operation import Operation
from app.models.licence import NavalClearanceVessel
from app.models.bdn import VesselActivity
from app.models.notification_log import ClientNotificationLog, PendingClientNotification
from app.models.audit import AuditLog
from app.models.user import User
from app.services.eta_service import EtaService
from app.services.email_service import email_client_notification
from app.schemas.client_notification import QueueClientNotificationRequest


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _get_clearance_vessels(operation: Operation, db: AsyncSession) -> List[NavalClearanceVessel]:
    """Every client-vessel reachable through any of the operation's linked
    clearances — empty if none linked, consistent with the link being
    optional and never a gate. Pools across all linked clearances, not just
    one, now that an operation can hold more than one."""
    clearance_ids = [link.naval_clearance_id for link in operation.naval_clearances]
    if not clearance_ids:
        return []
    result = await db.execute(
        select(NavalClearanceVessel)
        .options(selectinload(NavalClearanceVessel.client))
        .where(NavalClearanceVessel.naval_clearance_id.in_(clearance_ids))
    )
    return list(result.scalars().all())


async def _get_cast_off_recipients(operation_id: UUID, db: AsyncSession) -> List[dict]:
    """Every client email captured at Cast Off across this operation's
    vessel runs — the second recipient source, alongside Naval Clearance
    vessels. One dict per email, flattened out of each run's list."""
    result = await db.execute(select(VesselActivity).where(VesselActivity.operation_id == operation_id))
    recipients = []
    for activity in result.scalars().all():
        for email in (activity.cast_off_client_emails or []):
            if not email:
                continue
            recipients.append({
                "source": "cast_off",
                "naval_clearance_vessel_id": None,
                "client_id": None,
                "client_name": activity.cast_off_client_name,
                "client_email": email,
                "vessel_name": activity.cast_off_client_vessel_name or "—",
                "imo_number": None,
                "current_eta": None,
            })
    return recipients


def _dedupe_recipients(recipients: List[dict]) -> List[dict]:
    """Merge by lower-cased email. If a Cast Off email matches a Naval
    Clearance vessel's client email exactly, the Naval-Clearance-sourced
    entry wins (it carries a real client_id/naval_clearance_vessel_id) and
    the Cast Off duplicate is dropped."""
    by_email: dict = {}
    for r in recipients:
        email = (r.get("client_email") or "").strip().lower()
        if not email:
            continue
        existing = by_email.get(email)
        if existing is None or (existing["source"] == "cast_off" and r["source"] == "naval_clearance"):
            by_email[email] = r
    return list(by_email.values())


class ClientNotificationService:

    @staticmethod
    async def list_eligible_recipients(operation_id: UUID, db: AsyncSession) -> List[dict]:
        operation = await _get_operation_or_404(operation_id, db)
        vessels = await _get_clearance_vessels(operation, db)
        nc_recipients = []
        for v in vessels:
            if not v.client:
                continue
            eta = await EtaService.get_current_eta(v.id, db)
            nc_recipients.append({
                "source": "naval_clearance",
                "naval_clearance_vessel_id": v.id,
                "client_id": v.client_id,
                "client_name": v.client.full_name,
                "client_email": v.client.email,
                "vessel_name": v.vessel_name,
                "imo_number": v.imo_number,
                "current_eta": eta.eta_at if eta else None,
            })
        cast_off_recipients = await _get_cast_off_recipients(operation_id, db)
        return _dedupe_recipients(nc_recipients + cast_off_recipients)

    @staticmethod
    def _render_content(
        operation: Operation, vessel_name: str, notification_type: str,
        eta_at: Optional[datetime], custom_message: Optional[str],
    ) -> tuple[str, str]:
        """Isolated, single-recipient content — only this vessel's own
        details, never anything about another client on the same clearance."""
        if notification_type == "eta_change":
            subject = f"Updated ETA — {vessel_name} ({operation.operation_number})"
            body = f"The estimated time of arrival for your vessel <strong>{vessel_name}</strong> has been updated" + (
                f" to <strong>{eta_at.strftime('%d %b %Y, %H:%M')} UTC</strong>." if eta_at else "."
            )
        elif notification_type == "completion":
            subject = f"Delivery Completed — {vessel_name} ({operation.operation_number})"
            body = f"Delivery to <strong>{vessel_name}</strong> for operation {operation.operation_number} is complete."
        elif notification_type == "stage_update":
            subject = f"Delivery Update — {vessel_name} ({operation.operation_number})"
            body = custom_message or f"There is an update on the delivery to <strong>{vessel_name}</strong>."
        else:
            subject = f"Update — {vessel_name} ({operation.operation_number})"
            body = custom_message or f"There is an update regarding <strong>{vessel_name}</strong>."

        if custom_message and notification_type != "stage_update":
            body += f"<br/><br/>{custom_message}"

        return subject, body

    @staticmethod
    async def queue_client_notification(
        operation_id: UUID, data: QueueClientNotificationRequest, current_user: User, db: AsyncSession,
    ) -> List[PendingClientNotification]:
        """Nothing is sent here — this only records who's queued and what
        they'll receive. Recipients are re-verified server-side against the
        operation's own pool, never trusted from the client payload."""
        operation = await _get_operation_or_404(operation_id, db)
        vessels = await _get_clearance_vessels(operation, db)
        vessels_by_id = {v.id: v for v in vessels}
        cast_off_by_email = {r["client_email"].strip().lower(): r for r in await _get_cast_off_recipients(operation_id, db)}

        for rid in data.recipient_naval_clearance_vessel_ids:
            if rid not in vessels_by_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Recipient {rid} does not belong to any Naval Clearance linked to this operation",
                )
        for email in data.recipient_cast_off_emails:
            if email.strip().lower() not in cast_off_by_email:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"{email} is not a Cast Off client email recorded on this operation",
                )

        queued: List[PendingClientNotification] = []
        for rid in data.recipient_naval_clearance_vessel_ids:
            vessel = vessels_by_id[rid]
            if not vessel.client:
                continue
            eta = await EtaService.get_current_eta(rid, db)
            subject, body = ClientNotificationService._render_content(
                operation, vessel.vessel_name, data.notification_type, eta.eta_at if eta else None, data.custom_message,
            )
            queued.append(PendingClientNotification(
                operation_id=operation.id, naval_clearance_vessel_id=rid, client_id=vessel.client_id,
                source="naval_clearance", recipient_email=vessel.client.email, recipient_name=vessel.client.full_name,
                notification_type=data.notification_type, stage=data.stage, subject=subject, body_snapshot=body,
                requested_by=current_user.id,
            ))

        for email in data.recipient_cast_off_emails:
            r = cast_off_by_email[email.strip().lower()]
            subject, body = ClientNotificationService._render_content(
                operation, r["vessel_name"], data.notification_type, None, data.custom_message,
            )
            queued.append(PendingClientNotification(
                operation_id=operation.id, naval_clearance_vessel_id=None, client_id=None,
                source="cast_off", recipient_email=r["client_email"], recipient_name=r["client_name"],
                notification_type=data.notification_type, stage=data.stage, subject=subject, body_snapshot=body,
                requested_by=current_user.id,
            ))

        for q in queued:
            db.add(q)

        if queued:
            db.add(AuditLog(
                user_id=current_user.id, operation_id=operation.id, action="QUEUE_CLIENT_NOTIFICATION",
                entity_type="client_notification", entity_id=operation.id,
                changes={"notification_type": data.notification_type, "recipient_count": len(queued)},
            ))

        await db.flush()
        for q in queued:
            await db.refresh(q)
        return queued

    @staticmethod
    async def approve_pending_notifications(
        operation_id: UUID, pending_ids: List[UUID], current_user: User, db: AsyncSession,
    ) -> List[PendingClientNotification]:
        result = await db.execute(select(PendingClientNotification).where(and_(
            PendingClientNotification.id.in_(pending_ids),
            PendingClientNotification.operation_id == operation_id,
        )))
        rows = list(result.scalars().all())
        if len(rows) != len(set(pending_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="One or more queued notifications not found on this operation")

        for row in rows:
            if row.status != "pending_approval":
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot approve a notification with status '{row.status}'")
            row.status = "approved"
            row.approved_by = current_user.id
            row.approved_at = datetime.utcnow()

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation_id, action="APPROVE_CLIENT_NOTIFICATION",
            entity_type="client_notification", entity_id=operation_id,
            changes={"pending_ids": [str(r.id) for r in rows]},
        ))
        await db.flush()
        for row in rows:
            await db.refresh(row)
        return rows

    @staticmethod
    async def send_approved_notifications(
        operation_id: UUID, pending_ids: List[UUID], current_user: User, db: AsyncSession,
    ) -> List[ClientNotificationLog]:
        operation = await _get_operation_or_404(operation_id, db)
        result = await db.execute(select(PendingClientNotification).where(and_(
            PendingClientNotification.id.in_(pending_ids),
            PendingClientNotification.operation_id == operation_id,
        )))
        rows = list(result.scalars().all())
        if len(rows) != len(set(pending_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="One or more approved notifications not found on this operation")

        sent_logs: List[ClientNotificationLog] = []
        for row in rows:
            if row.status != "approved":
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot send a notification with status '{row.status}' — it must be approved first",
                )

            await email_client_notification(
                to_email=row.recipient_email, recipient_name=row.recipient_name or row.recipient_email,
                subject=row.subject, body_html=row.body_snapshot,
            )

            log = ClientNotificationLog(
                operation_id=operation.id,
                naval_clearance_vessel_id=row.naval_clearance_vessel_id,
                client_id=row.client_id,
                recipient_email=row.recipient_email,
                recipient_name=row.recipient_name or row.recipient_email,
                notification_type=row.notification_type,
                stage=row.stage,
                subject=row.subject,
                body_snapshot=row.body_snapshot,
                sent_by=current_user.id,
                thread_key=str(operation.id),
            )
            db.add(log)
            await db.flush()
            row.status = "sent"
            row.sent_log_id = log.id
            sent_logs.append(log)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="SEND_CLIENT_NOTIFICATION",
            entity_type="client_notification", entity_id=operation.id,
            changes={"recipient_count": len(sent_logs), "pending_ids": [str(r.id) for r in rows]},
        ))
        await db.flush()
        for log in sent_logs:
            await db.refresh(log)
        return sent_logs

    @staticmethod
    async def reject_pending_notification(
        operation_id: UUID, pending_id: UUID, reason: str, current_user: User, db: AsyncSession,
    ) -> PendingClientNotification:
        result = await db.execute(select(PendingClientNotification).where(and_(
            PendingClientNotification.id == pending_id,
            PendingClientNotification.operation_id == operation_id,
        )))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Queued notification not found")
        if row.status not in ("pending_approval", "approved"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot reject a notification with status '{row.status}'")
        row.status = "rejected"

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation_id, action="REJECT_CLIENT_NOTIFICATION",
            entity_type="client_notification", entity_id=row.id,
            changes={"reason": reason},
        ))
        await db.flush()
        await db.refresh(row)
        return row

    @staticmethod
    async def get_pending_notifications(operation_id: UUID, db: AsyncSession) -> List[PendingClientNotification]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(PendingClientNotification)
            .where(and_(
                PendingClientNotification.operation_id == operation_id,
                PendingClientNotification.status.in_(["pending_approval", "approved"]),
            ))
            .order_by(PendingClientNotification.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_notification_log(operation_id: UUID, db: AsyncSession) -> List[ClientNotificationLog]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(ClientNotificationLog)
            .where(ClientNotificationLog.operation_id == operation_id)
            .order_by(ClientNotificationLog.sent_at.desc())
        )
        return list(result.scalars().all())
