import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class VesselEta(Base):
    """Append-only ETA history for a client's receiving vessel on a Naval
    Clearance — no update/delete. "Current" ETA is the latest row; the
    previous one stays visible alongside it for planned-vs-actual review.
    Never overwritten, per the explicit requirement that a changed ETA keep
    its history."""
    __tablename__ = "vessel_etas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    naval_clearance_vessel_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearance_vessels.id", ondelete="CASCADE"), nullable=False)
    eta_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(Text, nullable=True)  # why it changed (weather, berth delay, etc.)
    set_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    setter = relationship("User", foreign_keys=[set_by])


class ClientNotificationLog(Base):
    """One row per recipient per send — never a multi-recipient row, no CC
    field. This is what makes "no path for one client to see another's
    data" a schema-level property, not just an application-logic promise."""
    __tablename__ = "client_notification_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    # Nullable — a Cast Off recipient has no NavalClearanceVessel/User behind
    # it, just a raw email/name the BM typed in (see PendingClientNotification
    # below, which fans out into rows here once actually sent).
    naval_clearance_vessel_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearance_vessels.id"), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Denormalized — survive the recipient's profile changing later.
    recipient_email = Column(String(255), nullable=False)
    recipient_name = Column(String(150), nullable=False)
    notification_type = Column(String(30), nullable=False)  # stage_update | eta_change | completion | general
    stage = Column(String(30), nullable=True)
    subject = Column(String(255), nullable=False)
    body_snapshot = Column(Text, nullable=False)  # exact rendered content sent — the audit record of "what it contained"
    sent_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sent_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    thread_key = Column(String(64), nullable=False)  # = str(operation_id) — keeps concurrent operations' threads separate

    operation = relationship("Operation", foreign_keys=[operation_id])
    client = relationship("User", foreign_keys=[client_id])
    sender = relationship("User", foreign_keys=[sent_by])


class PendingClientNotification(Base):
    """A recipient queued for a client notification, gated behind explicit
    BM approval before it can be sent. status moves pending_approval ->
    approved -> sent; only "sent" ever writes a ClientNotificationLog row
    (via sent_log_id), which is what keeps that table's own meaning —
    "this was actually sent" — intact. Two recipient sources: Naval
    Clearance vessels (naval_clearance_vessel_id/client_id set) and Cast
    Off client emails (both null, just a raw email/name)."""
    __tablename__ = "pending_client_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    naval_clearance_vessel_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearance_vessels.id"), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    source = Column(String(20), nullable=False)  # "naval_clearance" | "cast_off"
    recipient_email = Column(String(255), nullable=False)
    recipient_name = Column(String(150), nullable=True)
    notification_type = Column(String(30), nullable=False)
    stage = Column(String(30), nullable=True)
    subject = Column(String(255), nullable=False)
    body_snapshot = Column(Text, nullable=False)
    status = Column(String(20), default="pending_approval", nullable=False)
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    sent_log_id = Column(UUID(as_uuid=True), ForeignKey("client_notification_logs.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", foreign_keys=[operation_id])
    client = relationship("User", foreign_keys=[client_id])
    requester = relationship("User", foreign_keys=[requested_by])
    approver = relationship("User", foreign_keys=[approved_by])
    sent_log = relationship("ClientNotificationLog", foreign_keys=[sent_log_id])


class OperationNotification(Base):
    """One BM-triggered General notification, sent to a picked set of
    internal staff — a second, wholly separate channel from the automatic
    role-scoped notifications (finance on finance events, task-assignees on
    task events), sharing nothing with them but the base notify() helper."""
    __tablename__ = "operation_notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    sent_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", foreign_keys=[operation_id])
    sender = relationship("User", foreign_keys=[sent_by])
    recipients = relationship("OperationNotificationRecipient", back_populates="operation_notification")


class OperationNotificationRecipient(Base):
    """One row per recipient of a General notification — mirrors
    ClientNotificationLog's one-row-per-recipient shape. notification_id
    links to the recipient's own in-app Notification row so it appears in
    their normal feed like anything else."""
    __tablename__ = "operation_notification_recipients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_notification_id = Column(UUID(as_uuid=True), ForeignKey("operation_notifications.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notification_id = Column(UUID(as_uuid=True), ForeignKey("notifications.id"), nullable=True)

    operation_notification = relationship("OperationNotification", back_populates="recipients")
    user = relationship("User", foreign_keys=[user_id])
