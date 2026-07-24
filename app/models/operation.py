import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Numeric, ForeignKey, Integer,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import (
    OperationType, OperationStatus, TaskType, TaskStatus, Priority, FeedbackStatus
)


class Operation(Base):
    __tablename__ = "operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_number = Column(String(20), unique=True, nullable=False)
    type = Column(SAEnum(OperationType, name="operation_type"), nullable=False)
    status = Column(
        SAEnum(OperationStatus, name="operation_status"),
        nullable=False,
        default=OperationStatus.draft,
    )
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    expected_volume_mt = Column(Numeric(12, 3), nullable=True)
    actual_volume_mt = Column(Numeric(12, 3), nullable=True)
    product_type = Column(String(50), nullable=True)
    loading_location = Column(String(255), nullable=True)
    discharge_location = Column(String(255), nullable=True)
    trucks_required = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    paused_reason = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completion_notes = Column(Text, nullable=True)
    currency = Column(String(3), default="NGN", nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)
    # PFI-first flow: link to a pre-existing paid PFI
    pfi_id = Column(UUID(as_uuid=True), ForeignKey("pfis.id"), nullable=True)
    # Optional, linkable any time, never a gate — see NavalClearanceService.
    naval_clearance_id = Column(UUID(as_uuid=True), ForeignKey("naval_clearances.id"), nullable=True)
    color = Column(String(20), nullable=True)
    # Versioning / reopen support
    version = Column(Integer, default=1, nullable=False)
    parent_operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    version_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    client = relationship("User", foreign_keys=[client_id], back_populates="client_operations")
    creator = relationship("User", foreign_keys=[created_by], back_populates="created_operations")
    vessel = relationship("Vessel", back_populates="operations")
    naval_clearance = relationship("NavalClearance", foreign_keys=[naval_clearance_id], lazy="selectin")
    parent_operation = relationship("Operation", remote_side="Operation.id", foreign_keys=[parent_operation_id])
    child_operations = relationship("Operation", foreign_keys=[parent_operation_id], back_populates="parent_operation")
    status_history = relationship("OperationStatusHistory", back_populates="operation", order_by="OperationStatusHistory.created_at")
    task_assignments = relationship("TaskAssignment", back_populates="operation")
    truck_feedback = relationship("TruckFeedback", back_populates="operation")
    truck_operations = relationship("TruckOperation", back_populates="operation")
    rob_entries = relationship("RobEntry", back_populates="operation")
    vessel_discharge_events = relationship("VesselDischargeEvent", back_populates="operation")
    vessel_activities = relationship("VesselActivity", back_populates="operation")
    bdns = relationship("BDN", back_populates="operation")
    truck_bdns = relationship("TruckBdn", back_populates="operation")
    pfis = relationship("PFI", foreign_keys="[PFI.operation_id]", back_populates="operation")
    source_pfi = relationship("PFI", foreign_keys="[Operation.pfi_id]", uselist=False)
    pfi_allocations = relationship("PfiAllocation", back_populates="operation")
    # selectin: every Operation serialized to OperationOut needs .products, and
    # there are many call sites across services/routers that fetch a bare
    # Operation without explicit eager-loading — always-on selectin avoids
    # having to remember selectinload() at each one (async sessions can't
    # lazy-load on demand).
    products = relationship("OperationProduct", back_populates="operation", cascade="all, delete-orphan", lazy="selectin")
    payments = relationship("Payment", back_populates="operation")
    invoices = relationship("Invoice", back_populates="operation")
    vouchers = relationship("Voucher", back_populates="operation")
    documents = relationship("Document", back_populates="operation")
    notifications = relationship("Notification", back_populates="operation")
    audit_logs = relationship("AuditLog", back_populates="operation")
    milestones = relationship("ClientMilestone", back_populates="operation")


class OperationProduct(Base):
    """One product+quantity line on an operation. An operation always has at
    least one — this fully replaces the old single product_type/expected_volume_mt
    fields (which stay on Operation, unused, for additive-migration safety)."""
    __tablename__ = "operation_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    product_type = Column(String(50), nullable=False)
    quantity_mt = Column(Numeric(12, 3), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="products")


class OperationStatusHistory(Base):
    __tablename__ = "operation_status_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    from_status = Column(SAEnum(OperationStatus, name="operation_status"), nullable=True)
    to_status = Column(SAEnum(OperationStatus, name="operation_status"), nullable=False)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, default={}, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="status_history")
    changed_by_user = relationship("User", foreign_keys=[changed_by])


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    task_type = Column(SAEnum(TaskType, name="task_type"), nullable=False)
    status = Column(SAEnum(TaskStatus, name="task_status"), default=TaskStatus.pending, nullable=False)
    priority = Column(SAEnum(Priority, name="priority"), default=Priority.normal, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    instructions = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="task_assignments")
    assignee = relationship("User", foreign_keys=[assigned_to], back_populates="task_assignments_received")
    assigner = relationship("User", foreign_keys=[assigned_by], back_populates="task_assignments_given")


class TruckFeedback(Base):
    __tablename__ = "truck_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    submitted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    truck_ids = Column(JSONB, nullable=False)
    status = Column(SAEnum(FeedbackStatus, name="feedback_status"), default=FeedbackStatus.pending, nullable=False)
    readiness_summary = Column(Text, nullable=False)
    truck_details = Column(JSONB, nullable=False)
    rejection_reason = Column(Text, nullable=True)
    submitted_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, default=1, nullable=False)

    operation = relationship("Operation", back_populates="truck_feedback")
    submitter = relationship("User", foreign_keys=[submitted_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
