import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Numeric, ForeignKey,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import TruckStatus, TruckOpStatus, AuditResult


class Truck(Base):
    __tablename__ = "trucks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_number = Column(String(50), unique=True, nullable=False)
    capacity_mt = Column(Numeric(10, 3), nullable=False)
    driver_name = Column(String(150), nullable=True)
    driver_phone = Column(String(20), nullable=True)
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


class TruckOperation(Base):
    """Full telemetry record for a single truck within an operation."""
    __tablename__ = "truck_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    truck_id = Column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)
    logged_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

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
    rob_entries = relationship("RobEntry", back_populates="truck_operation")
    safety_audit = relationship("TruckSafetyAudit", back_populates="truck_operation", uselist=False)


class TruckSafetyAudit(Base):
    """Pre-operation safety audit for a single truck within an operation. One per truck_op."""
    __tablename__ = "truck_safety_audits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    truck_op_id = Column(UUID(as_uuid=True), ForeignKey("truck_operations.id", ondelete="CASCADE"), nullable=False, unique=True)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    truck_id = Column(UUID(as_uuid=True), ForeignKey("trucks.id"), nullable=False)
    conducted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    conductor_name = Column(String(150), nullable=True)
    conducted_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    result = Column(SAEnum(AuditResult, name="audit_result"), nullable=False)
    checklist = Column(JSONB, default=list, nullable=False)
    notes = Column(Text, nullable=True)
    waivers = Column(JSONB, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    truck_operation = relationship("TruckOperation", back_populates="safety_audit")
    conductor = relationship("User", foreign_keys=[conducted_by])
