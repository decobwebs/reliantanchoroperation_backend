import uuid
from datetime import datetime

from sqlalchemy import Column, String, Date, DateTime, Text, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# Plain strings, validated in the schemas — see migration 074. Types and
# severities are as drafted in SYSTEM-UPDATE-PLAN.md, agreed 1 Nov 2026.
INCIDENT_TYPES = (
    "spill", "injury", "near_miss", "equipment_failure", "collision",
    "fire", "pollution", "security", "other",
)
INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
INCIDENT_STATUSES = ("open", "under_investigation", "closed")

MAINTENANCE_TYPES = (
    "planned_service", "breakdown_repair", "inspection", "dry_dock", "certificate_renewal",
)
MAINTENANCE_STATUSES = ("planned", "in_progress", "done", "cancelled")


class VesselIncident(Base):
    """Something that went wrong on or around one of our barges — a spill, an
    injury, a near miss. Kept on the vessel; an operation or vessel run can be
    referenced when it happened during one.

    Separate from the KPI's "safety incidents" (failed HSE checks + spillage
    on a vessel run), which stay as they are until real incidents are being
    logged — see SYSTEM-UPDATE-PLAN.md 3.5.
    """
    __tablename__ = "vessel_incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False, index=True)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id"), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    location = Column(Text, nullable=True)
    incident_type = Column(String(30), nullable=False)
    severity = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    immediate_action = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="open")
    root_cause = Column(Text, nullable=True)
    corrective_action = Column(Text, nullable=True)
    reported_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    closed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    attachments = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel")
    operation = relationship("Operation")
    vessel_activity = relationship("VesselActivity")
    reporter = relationship("User", foreign_keys=[reported_by])
    closer = relationship("User", foreign_keys=[closed_by])


class VesselMaintenance(Base):
    """One piece of maintenance on a barge: a service, a repair, an inspection,
    a dry dock or a certificate renewal.

    Records sharing a type and title are one series ("Main engine service",
    "Class certificate"); the newest sets when the next one is due. Starting
    work never changes the vessel's own status — a vessel can be partway
    through a run — so the page suggests the change and a person makes it.
    """
    __tablename__ = "vessel_maintenance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False, index=True)
    maintenance_type = Column(String(30), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="planned")
    planned_date = Column(Date, nullable=True)
    completed_date = Column(Date, nullable=True)
    next_due_date = Column(Date, nullable=True)
    performed_by = Column(String(200), nullable=True)
    cost = Column(Numeric(14, 2), nullable=True)
    cost_currency = Column(String(3), nullable=True)
    attachments = Column(JSONB, nullable=False, default=list)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel")
    creator = relationship("User", foreign_keys=[created_by])
