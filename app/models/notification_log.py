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
    naval_clearance_vessel_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearance_vessels.id"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
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
