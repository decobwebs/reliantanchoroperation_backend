import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Text, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base

# Plain strings, validated in the service — see migration 071.
STS_STATUSES = ("in_progress", "pending", "approved", "rejected", "cancelled")
STS_STAGES = ("cast_off", "alongside", "receiving_started", "receiving_completed")
STS_HSE_PHASES = ("pre", "during", "post")


class StsCollection(Base):
    """One trip by our barge to collect product from another vessel, ship to
    ship. A job may carry any number of them alongside its trucks; approved
    collections count towards the operation's Total Loaded Quantity.

    The receipt BDN lives on the same row (receipt_number + figures +
    review fields) rather than in `bdns`: approving every row in `bdns` is
    what clears an operation's BDN gate, and a collection must not start
    gating delivery paperwork. Approval credits the barge's ROB with
    mt_vacuum through a RobEntry tagged with the receipt number, so a later
    correction or cancellation can find and reverse exactly that entry.
    """
    __tablename__ = "sts_collections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False, index=True)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    source_vessel_name = Column(String(200), nullable=False)
    source_vessel_imo = Column(String(20), nullable=True)
    product_type = Column(String(50), nullable=True)
    location = Column(Text, nullable=True)

    status = Column(String(20), nullable=False, default="in_progress")
    stage = Column(String(30), nullable=True)
    cast_off_at = Column(DateTime(timezone=True), nullable=True)
    alongside_at = Column(DateTime(timezone=True), nullable=True)
    receiving_started_at = Column(DateTime(timezone=True), nullable=True)
    receiving_completed_at = Column(DateTime(timezone=True), nullable=True)

    hse = Column(JSONB, nullable=False, default=dict)

    receipt_number = Column(String(20), nullable=True, unique=True)
    gov = Column(Numeric(14, 2), nullable=True)
    gsv = Column(Numeric(14, 2), nullable=True)
    mt_vacuum = Column(Numeric(12, 3), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    vcf = Column(Numeric(8, 4), nullable=True)
    temperature = Column(Numeric(6, 2), nullable=True)
    submitted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel", foreign_keys=[vessel_id])
    creator = relationship("User", foreign_keys=[created_by])
    submitter = relationship("User", foreign_keys=[submitted_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
