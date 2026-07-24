import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, Date, DateTime, Text, Numeric, Integer,
    ForeignKey, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class Ppdl(Base):
    """Top of the licence chain — PPDL (annual), issued once and renewed
    each year. Every operation automatically carries whichever PPDL is
    `is_current` at the time it's created; operations already created keep
    the PPDL they were created under. Exactly one row may be current at a
    time (enforced by a partial unique index in the migration)."""
    __tablename__ = "ppdls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ppdl_number = Column(String(30), unique=True, nullable=False)
    issue_date = Column(Date, nullable=False)
    expiry_date = Column(Date, nullable=False)
    is_current = Column(Boolean, default=False, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    products = relationship("PpdlProduct", back_populates="ppdl", cascade="all, delete-orphan")
    bfls = relationship("Bfl", back_populates="ppdl")
    creator = relationship("User", foreign_keys=[created_by])


class PpdlProduct(Base):
    """One product+quantity line on a PPDL — mirrors OperationProduct's
    one-parent/many-product-lines shape. Balance for this product is always
    computed on read (quantity_litres minus the sum of every Bfl drawn
    against this ppdl_id+product_type), never stored."""
    __tablename__ = "ppdl_products"
    __table_args__ = (UniqueConstraint("ppdl_id", "product_type", name="uq_ppdl_product"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ppdl_id = Column(UUID(as_uuid=True), ForeignKey("ppdls.id", ondelete="CASCADE"), nullable=False)
    product_type = Column(String(50), nullable=False)
    quantity_litres = Column(Numeric(14, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    ppdl = relationship("Ppdl", back_populates="products")


class Bfl(Base):
    """BFL — sits under the current PPDL. Multiple BFLs can be active at
    once. Creating a BFL row IS the drawdown against its matching
    PpdlProduct — there is no separate allocation table for this tier,
    since a BFL draws exactly once, at creation, against exactly one
    product."""
    __tablename__ = "bfls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bfl_number = Column(String(30), unique=True, nullable=False)
    ppdl_id = Column(UUID(as_uuid=True), ForeignKey("ppdls.id"), nullable=False)
    product_type = Column(String(50), nullable=False)
    quantity_litres = Column(Numeric(14, 2), nullable=False)
    vessel = Column(String(200), nullable=True)
    expiry_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    ppdl = relationship("Ppdl", back_populates="bfls")
    creator = relationship("User", foreign_keys=[created_by])
    drawdowns = relationship("NavalClearanceDrawdown", back_populates="bfl")


class NavalClearance(Base):
    """Sits under one or more BFLs — the level an operation actually
    connects to. Drawdown is based on the cleared figure, not on what
    trucks actually load or discharge (those are tracked separately on the
    operation and never affect licence balances)."""
    __tablename__ = "naval_clearances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clearance_number = Column(String(30), unique=True, nullable=False)
    date_of_loading = Column(Date, nullable=False)
    expiry_date = Column(Date, nullable=False)
    document_url = Column(Text, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    drawdowns = relationship("NavalClearanceDrawdown", back_populates="naval_clearance", cascade="all, delete-orphan")
    loading_locations = relationship("NavalClearanceLoadingLocation", back_populates="naval_clearance", cascade="all, delete-orphan")
    vessels = relationship("NavalClearanceVessel", back_populates="naval_clearance", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])
    operations = relationship("Operation", foreign_keys="[Operation.naval_clearance_id]")


class NavalClearanceDrawdown(Base):
    """One row per BFL selected on a Naval Clearance — a clearance may draw
    from more than one BFL, and each drawdown's product is always read
    through its BFL (satisfies "may carry more than one product" without a
    separate products field on the parent)."""
    __tablename__ = "naval_clearance_drawdowns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    naval_clearance_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False)
    bfl_id = Column(UUID(as_uuid=True), ForeignKey("bfls.id"), nullable=False)
    quantity_litres = Column(Numeric(14, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    naval_clearance = relationship("NavalClearance", back_populates="drawdowns")
    bfl = relationship("Bfl", back_populates="drawdowns")


class NavalClearanceLoadingLocation(Base):
    """A clearance may carry more than one loading location — always
    addable, never a fixed count."""
    __tablename__ = "naval_clearance_loading_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    naval_clearance_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False)
    location = Column(String(255), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    naval_clearance = relationship("NavalClearance", back_populates="loading_locations")


class NavalClearanceVessel(Base):
    """One row per client-vessel pair on a clearance — a client may have
    more than one vessel on the same clearance. The vessel name/IMO is
    entered here, per-clearance, never permanently bound to the client,
    since the same physical ship can belong to different clients on
    different operations."""
    __tablename__ = "naval_clearance_vessels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    naval_clearance_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    vessel_name = Column(String(200), nullable=False)
    imo_number = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    naval_clearance = relationship("NavalClearance", back_populates="vessels")
    client = relationship("User", foreign_keys=[client_id])
