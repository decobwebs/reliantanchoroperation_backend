import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# Plain strings, validated in the schemas — see migration 075.
INSPECTION_SUBJECTS = ("vessel", "truck", "operation", "site")
INSPECTION_RESULTS = ("pass", "pass_with_findings", "fail")
ACTION_STATUSES = ("open", "done")


class Inspection(Base):
    """An inspection or compliance check by the Safety Officer (or the BM):
    what was looked at, when, what was found, and what must be fixed.

    The first version is a simple form, as agreed on 1 Nov 2026 — no
    checklist templates until management supplies the checklists to use.
    What was inspected is one of a vessel, a truck, an operation or a named
    site; only the matching reference is filled.
    """
    __tablename__ = "inspections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject_type = Column(String(20), nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True, index=True)
    truck_id = Column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=True, index=True)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    site_name = Column(String(200), nullable=True)
    inspected_on = Column(Date, nullable=False)
    title = Column(String(200), nullable=False)
    result = Column(String(30), nullable=False)
    findings = Column(Text, nullable=True)
    inspector_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    attachments = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel")
    truck = relationship("Truck")
    operation = relationship("Operation")
    inspector = relationship("User", foreign_keys=[inspector_id])
    actions = relationship("InspectionAction", back_populates="inspection", order_by="InspectionAction.created_at")


class InspectionAction(Base):
    """A fix an inspection asks for: what, who owns it, and by when. Closed by
    the Safety Officer or the BM with a note on what was done."""
    __tablename__ = "inspection_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inspection_id = Column(UUID(as_uuid=True), ForeignKey("inspections.id"), nullable=False, index=True)
    description = Column(Text, nullable=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="open")
    done_note = Column(Text, nullable=True)
    done_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    done_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    inspection = relationship("Inspection", back_populates="actions")
    owner = relationship("User", foreign_keys=[owner_id])
    finisher = relationship("User", foreign_keys=[done_by])
