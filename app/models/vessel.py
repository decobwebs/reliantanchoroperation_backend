import logging
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Numeric, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import event
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import VesselStatus

logger = logging.getLogger(__name__)


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


# ── ROB sanity warning ────────────────────────────────────────────────────────
# `current_rob_mt` is a denormalised running total mutated from eight different
# service paths (truck discharge, corrections, vessel activities, operation
# close-out...). Putting the range check on each of them would duplicate the
# rule and let the copies drift apart, so it hooks the attribute itself: every
# write goes through here regardless of which path made it.
#
# Warn-only by design — an out-of-range figure is almost always a units or
# decimal-point slip (a 587 L vessel was once sent a 204,020 L discharge), but
# blocking the write outright could strand a legitimate correction mid-flow.
# The UI surfaces the same condition to the operator.
@event.listens_for(Vessel.current_rob_mt, "set")
def _warn_rob_out_of_range(target, value, oldvalue, initiator):
    if value is None:
        return value
    try:
        new = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value

    if new < 0:
        logger.warning(
            "ROB out of range: vessel=%s (%s) set to %s — below zero, "
            "likely a units or decimal-point error",
            getattr(target, "vessel_name", "?"), getattr(target, "id", "?"), new,
        )
    elif target.capacity_mt is not None:
        cap = Decimal(str(target.capacity_mt))
        if cap > 0 and new > cap:
            logger.warning(
                "ROB out of range: vessel=%s (%s) set to %s — exceeds capacity %s",
                getattr(target, "vessel_name", "?"), getattr(target, "id", "?"), new, cap,
            )
    return value
