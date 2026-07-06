import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Numeric, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import VesselStatus


class Vessel(Base):
    __tablename__ = "vessels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_name = Column(String(200), nullable=False)
    imo_number = Column(String(20), unique=True, nullable=True)
    vessel_type = Column(String(100), nullable=True)
    flag_state = Column(String(100), nullable=True)
    capacity_mt = Column(Numeric(12, 3), nullable=True)
    current_rob_mt = Column(Numeric(12, 3), default=0, nullable=False)
    rob_threshold_mt = Column(Numeric(12, 3), default=100, nullable=False)
    current_location = Column(Text, nullable=True)
    status = Column(SAEnum(VesselStatus, name="vessel_status"), default=VesselStatus.available, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    operations = relationship("Operation", back_populates="vessel")
    rob_entries = relationship("RobEntry", foreign_keys="RobEntry.vessel_id", back_populates="vessel")
    bdns = relationship("BDN", back_populates="vessel")
    truck_operations = relationship("TruckOperation", foreign_keys="TruckOperation.destination_vessel_id", back_populates="destination_vessel")
    discharge_events_as_source = relationship("VesselDischargeEvent", foreign_keys="VesselDischargeEvent.source_vessel_id", back_populates="source_vessel")
    discharge_events_as_dest = relationship("VesselDischargeEvent", foreign_keys="VesselDischargeEvent.destination_vessel_id", back_populates="destination_vessel")
    vessel_activities = relationship("VesselActivity", back_populates="vessel")
