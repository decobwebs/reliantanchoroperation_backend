import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Text, Numeric, ForeignKey, Integer,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import RobEntryType, BdnStatus, VesselActivityStatus


class RobEntry(Base):
    """Immutable ledger of every ROB change on a vessel. Never updated or deleted."""
    __tablename__ = "rob_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    entry_type = Column(SAEnum(RobEntryType, name="rob_entry_type"), nullable=False)
    quantity_mt = Column(Numeric(12, 3), nullable=False)   # signed; discharge is negative
    rob_before_mt = Column(Numeric(12, 3), nullable=False)
    rob_after_mt = Column(Numeric(12, 3), nullable=False)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Source traceability
    truck_operation_id = Column(UUID(as_uuid=True), ForeignKey("truck_operations.id"), nullable=True)
    source_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Physical measurements
    spillage_mt = Column(Numeric(12, 3), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)

    source_description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel", foreign_keys=[vessel_id], back_populates="rob_entries")
    source_vessel = relationship("Vessel", foreign_keys=[source_vessel_id])
    operation = relationship("Operation", back_populates="rob_entries")
    recorder = relationship("User", foreign_keys=[recorded_by])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    truck_operation = relationship("TruckOperation", foreign_keys=[truck_operation_id], back_populates="rob_entries")


class BDN(Base):
    __tablename__ = "bdns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bdn_number = Column(String(20), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    generated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status = Column(SAEnum(BdnStatus, name="bdn_status"), default=BdnStatus.pending, nullable=False)
    quantity_delivered_mt = Column(Numeric(12, 3), nullable=False)
    product_type = Column(String(100), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    temperature = Column(Numeric(6, 2), nullable=True)
    delivery_date = Column(DateTime(timezone=True), nullable=False)
    rejection_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    pdf_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    version = Column(Integer, default=1, nullable=False)

    operation = relationship("Operation", back_populates="bdns")
    vessel = relationship("Vessel", back_populates="bdns")
    generator = relationship("User", foreign_keys=[generated_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    invoices = relationship("Invoice", back_populates="bdn")


class VesselDischargeEvent(Base):
    """Records a vessel-to-vessel or vessel-to-client discharge event."""
    __tablename__ = "vessel_discharge_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    source_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    destination_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)

    product_type = Column(String(50), nullable=True)
    quantity_mt = Column(Numeric(12, 3), nullable=False)
    spillage_mt = Column(Numeric(12, 3), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)

    discharge_start_at = Column(DateTime(timezone=True), nullable=True)
    discharge_end_at = Column(DateTime(timezone=True), nullable=True)

    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rob_entry_id = Column(UUID(as_uuid=True), ForeignKey("rob_entries.id"), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="vessel_discharge_events")
    source_vessel = relationship("Vessel", foreign_keys=[source_vessel_id], back_populates="discharge_events_as_source")
    destination_vessel = relationship("Vessel", foreign_keys=[destination_vessel_id], back_populates="discharge_events_as_dest")
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    rob_entry = relationship("RobEntry", foreign_keys=[rob_entry_id])


class VesselActivity(Base):
    """Marine Supervisor oversight session — tracks vessel bunkering/discharge quantities and ROB reconciliation."""
    __tablename__ = "vessel_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_number = Column(String(20), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Quantity tracking — Full Operation flow
    truck_delivered_mt = Column(Numeric(12, 3), nullable=True)      # total from truck operations
    vessel_received_mt = Column(Numeric(12, 3), nullable=True)      # measured at vessel
    variance_mt = Column(Numeric(12, 3), nullable=True)             # truck_delivered - vessel_received

    # ROB reconciliation
    initial_rob_mt = Column(Numeric(12, 3), nullable=True)           # BM-set vessel ROB at activity creation
    previous_rob_mt = Column(Numeric(12, 3), nullable=True)         # ROB before bunkering starts
    new_rob_mt = Column(Numeric(12, 3), nullable=True)              # previous_rob + vessel_received
    quantity_discharged_mt = Column(Numeric(12, 3), nullable=True)  # discharge to another vessel (optional)
    final_rob_mt = Column(Numeric(12, 3), nullable=True)            # new_rob - quantity_discharged

    # Physical measurements
    product_type = Column(String(50), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    spillage_mt = Column(Numeric(12, 3), nullable=True)

    # Bunkering / discharge timing
    bunkering_start_at = Column(DateTime(timezone=True), nullable=True)
    bunkering_end_at = Column(DateTime(timezone=True), nullable=True)
    discharge_start_at = Column(DateTime(timezone=True), nullable=True)
    discharge_end_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(SAEnum(VesselActivityStatus, name="vessel_activity_status"), default=VesselActivityStatus.pending, nullable=False)
    notes = Column(Text, nullable=True)
    completion_notes = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="vessel_activities")
    vessel = relationship("Vessel", back_populates="vessel_activities")
    assignee = relationship("User", foreign_keys=[assigned_to])
    assigner = relationship("User", foreign_keys=[assigned_by])
