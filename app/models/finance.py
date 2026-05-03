import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, DateTime, Date, Text, Numeric, ForeignKey, Integer,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import PfiStatus, InvoiceStatus, VoucherStatus, VoucherCategory


class PFI(Base):
    __tablename__ = "pfis"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pfi_number = Column(String(50), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)  # nullable — PFI can precede operation
    linked_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    currency = Column(String(3), default="NGN", nullable=False)
    exchange_rate = Column(Numeric(12, 6), nullable=True)
    amount_ngn = Column(Numeric(15, 2), nullable=True)
    supplier_name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    document_url = Column(Text, nullable=True)
    receipt_url = Column(Text, nullable=True)        # payment receipt uploaded by FM
    client_ref = Column(String(200), nullable=True)  # client's own PFI reference number
    confirmed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(SAEnum(PfiStatus, name="pfi_status"), default=PfiStatus.pending, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    # foreign_keys required: two FK paths exist between operations and pfis
    operation = relationship("Operation", foreign_keys=[operation_id], back_populates="pfis")
    linker = relationship("User", foreign_keys=[linked_by])
    confirmer = relationship("User", foreign_keys=[confirmed_by])
    payments = relationship("Payment", back_populates="pfi")
    vouchers = relationship("Voucher", back_populates="pfi")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pfi_id = Column(UUID(as_uuid=True), ForeignKey("pfis.id"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    processed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    payment_method = Column(String(50), nullable=True)
    payment_reference = Column(String(200), nullable=True)
    payment_date = Column(DateTime(timezone=True), nullable=False)
    voucher_number = Column(String(50), unique=True, nullable=False)
    voucher_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    pfi = relationship("PFI", back_populates="payments")
    operation = relationship("Operation", back_populates="payments")
    processor = relationship("User", foreign_keys=[processed_by])


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number = Column(String(50), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    bdn_id = Column(UUID(as_uuid=True), ForeignKey("bdns.id"), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    generated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    exchange_rate = Column(Numeric(12, 6), nullable=True)
    tax_amount = Column(Numeric(15, 2), default=0, nullable=False)
    total_amount = Column(Numeric(15, 2), nullable=False)
    due_date = Column(Date, nullable=True)
    status = Column(SAEnum(InvoiceStatus, name="invoice_status"), default=InvoiceStatus.draft, nullable=False)
    pdf_url = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="invoices")
    bdn = relationship("BDN", back_populates="invoices")
    client = relationship("User", foreign_keys=[client_id])
    generator = relationship("User", foreign_keys=[generated_by])


class Voucher(Base):
    """Expense/disbursement voucher — tracks outgoing costs for an operation."""
    __tablename__ = "vouchers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    voucher_number = Column(String(50), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    pfi_id = Column(UUID(as_uuid=True), ForeignKey("pfis.id"), nullable=True)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    category = Column(SAEnum(VoucherCategory, name="voucher_category"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    exchange_rate = Column(Numeric(12, 6), nullable=True)
    amount_ngn = Column(Numeric(15, 2), nullable=True)

    supplier_name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    receipt_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    status = Column(SAEnum(VoucherStatus, name="voucher_status"), default=VoucherStatus.draft, nullable=False)
    payment_date = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="vouchers")
    pfi = relationship("PFI", back_populates="vouchers")
    recorder = relationship("User", foreign_keys=[recorded_by])
    approver = relationship("User", foreign_keys=[approved_by])
