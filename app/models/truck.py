import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Numeric, ForeignKey,
    Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import TruckStatus, TruckOpStatus, AuditResult, TruckWaiverStatus, AuditPhase


class Truck(Base):
    """Fleet Library — the plate-identity profile. Keyed by the original plate
    number; reused across operations so history/photo/docs never need re-entry."""
    __tablename__ = "trucks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_number = Column(String(50), unique=True, nullable=False)
    capacity_mt = Column(Numeric(10, 3), nullable=False)
    # "Last known driver" cache. The authoritative, per-assignment driver now
    # lives on TruckOperation (drivers are temporary, not permanently tied to a
    # truck) — these columns are mirrored from there on sourcing/waybill-link so
    # existing fleet-list/detail UI still has something to display.
    driver_name = Column(String(150), nullable=True)
    driver_phone = Column(String(20), nullable=True)
    chassis_number = Column(String(100), nullable=True)
    truck_licence_url = Column(Text, nullable=True)
    calibration_cert_url = Column(Text, nullable=True)
    status = Column(SAEnum(TruckStatus, name="truck_status"), default=TruckStatus.available, nullable=False)
    current_location = Column(Text, nullable=True)
    gps_lat = Column(Numeric(10, 7), nullable=True)
    gps_lng = Column(Numeric(10, 7), nullable=True)
    photo_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    truck_operations = relationship("TruckOperation", back_populates="truck")


class TruckWaiver(Base):
    """Waiver / regulatory (BFL) truck number pool — bulk-added up front by Ops
    Supervisor, before any truck sourcing happens. Linked to a real plate number
    and driver only once the waybill is generated for a given truck_operation."""
    __tablename__ = "truck_waivers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    waybill_truck_number = Column(String(50), unique=True, nullable=False)
    status = Column(SAEnum(TruckWaiverStatus, name="truck_waiver_status"), default=TruckWaiverStatus.available, nullable=False)
    added_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    adder = relationship("User", foreign_keys=[added_by])


class TruckOperation(Base):
    """Full telemetry record for a single truck within an operation."""
    __tablename__ = "truck_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    truck_id = Column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)
    logged_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Sourcing (per-assignment — driver/vendor are temporary, not tied to the truck master)
    driver_name = Column(String(150), nullable=True)
    driver_phone = Column(String(20), nullable=True)
    vendor_name = Column(String(200), nullable=True)

    # Waybill — the moment the waiver number, plate, and driver are linked together
    waybill_document_number = Column(String(100), nullable=True)
    waiver_id = Column(UUID(as_uuid=True), ForeignKey("truck_waivers.id"), nullable=True)
    waybill_linked_at = Column(DateTime(timezone=True), nullable=True)

    # Product
    product_type = Column(String(50), nullable=True)

    # Quantities
    quantity_loaded_mt = Column(Numeric(12, 3), nullable=True)
    quantity_discharged_mt = Column(Numeric(12, 3), nullable=True)
    quantity_remaining_mt = Column(Numeric(12, 3), nullable=True)
    variance_mt = Column(Numeric(12, 3), nullable=True)
    spillage_mt = Column(Numeric(12, 3), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)

    # Locations
    loading_location = Column(Text, nullable=True)
    discharge_location = Column(Text, nullable=True)
    destination_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)
    destination_vessel_name = Column(Text, nullable=True)  # free-text for vessels not in system

    # Discharge approval gate (null = no vessel, False = pending BM approval, True = approved)
    discharge_approved = Column(Boolean, nullable=True)
    discharge_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    discharge_approved_at = Column(DateTime(timezone=True), nullable=True)

    # Full movement timeline
    departed_parking_at = Column(DateTime(timezone=True), nullable=True)   # left depot/yard
    arrived_loading_at = Column(DateTime(timezone=True), nullable=True)    # arrived at loading point
    departed_loading_at = Column(DateTime(timezone=True), nullable=True)   # left loading (loaded)
    transit_start_at = Column(DateTime(timezone=True), nullable=True)      # started transit to discharge
    arrived_discharge_at = Column(DateTime(timezone=True), nullable=True)  # arrived at discharge location
    transit_end_at = Column(DateTime(timezone=True), nullable=True)        # (alias) arrived at discharge
    discharge_start_at = Column(DateTime(timezone=True), nullable=True)    # discharge began
    discharge_end_at = Column(DateTime(timezone=True), nullable=True)      # discharge complete

    # GPS
    gps_start_lat = Column(Numeric(10, 7), nullable=True)
    gps_start_lng = Column(Numeric(10, 7), nullable=True)
    gps_end_lat = Column(Numeric(10, 7), nullable=True)
    gps_end_lng = Column(Numeric(10, 7), nullable=True)

    # Waybill
    waybill_number = Column(String(100), nullable=True)
    waybill_url = Column(Text, nullable=True)

    status = Column(SAEnum(TruckOpStatus, name="truck_op_status"), default=TruckOpStatus.pending, nullable=False)
    notes = Column(Text, nullable=True)
    events = Column(JSONB, default=list, nullable=False)  # [{ts, type, description, user_id}]

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="truck_operations")
    truck = relationship("Truck", back_populates="truck_operations")
    logger = relationship("User", foreign_keys=[logged_by])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    destination_vessel = relationship("Vessel", foreign_keys=[destination_vessel_id], back_populates="truck_operations")
    discharge_approver = relationship("User", foreign_keys=[discharge_approved_by])
    rob_entries = relationship("RobEntry", back_populates="truck_operation")
    safety_audits = relationship("TruckSafetyAudit", back_populates="truck_operation")
    waiver = relationship("TruckWaiver", foreign_keys=[waiver_id])


class TruckSafetyAudit(Base):
    """Safety audit for a single truck within an operation — one Pre (before
    loading) and one Post (before discharge) per truck_op, per `phase`."""
    __tablename__ = "truck_safety_audits"
    __table_args__ = (UniqueConstraint("truck_op_id", "phase", name="uq_truck_safety_audits_truck_op_phase"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_op_id = Column(UUID(as_uuid=True), ForeignKey("truck_operations.id", ondelete="CASCADE"), nullable=False)
    phase = Column(SAEnum(AuditPhase, name="audit_phase"), default=AuditPhase.pre, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    truck_id = Column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)
    conducted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    conductor_name = Column(String(150), nullable=True)
    conducted_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    result = Column(SAEnum(AuditResult, name="audit_result"), nullable=False)
    checklist = Column(JSONB, default=list, nullable=False)
    header = Column(JSONB, default=dict, nullable=False)  # Safety Officer, Driver, PFI, dates, etc.
    notes = Column(Text, nullable=True)
    waivers = Column(JSONB, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    truck_operation = relationship("TruckOperation", back_populates="safety_audits")
    conductor = relationship("User", foreign_keys=[conducted_by])
