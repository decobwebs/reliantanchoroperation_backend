import logging
import uuid
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from sqlalchemy import (
    Column, String, DateTime, Date, Text, Numeric, ForeignKey, Integer,
    Enum as SAEnum, event
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import PfiType, PfiStatus, InvoiceStatus, VoucherStatus, VoucherCategory

logger = logging.getLogger("raoms")


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
    quantity_litres = Column(Numeric(14, 2), nullable=True)  # total volume this PFI covers; drawn down via PfiAllocation
    supplier_name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    document_url = Column(Text, nullable=True)
    receipt_url = Column(Text, nullable=True)        # payment receipt uploaded by FM
    client_ref = Column(String(200), nullable=True)  # client's own PFI reference number
    confirmed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    pfi_type = Column(SAEnum(PfiType, name="pfi_type"), default=PfiType.client_proforma, nullable=False)
    status = Column(SAEnum(PfiStatus, name="pfi_status"), default=PfiStatus.pending, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    # foreign_keys required: two FK paths exist between operations and pfis
    operation = relationship("Operation", foreign_keys=[operation_id], back_populates="pfis")
    linker = relationship("User", foreign_keys=[linked_by])
    confirmer = relationship("User", foreign_keys=[confirmed_by])
    payments = relationship("Payment", back_populates="pfi")
    vouchers = relationship("Voucher", back_populates="pfi")
    allocations = relationship("PfiAllocation", back_populates="pfi", cascade="all, delete-orphan")


class PfiAllocation(Base):
    """A fixed quantity drawdown of a PFI against one operation. A PFI can be
    allocated across several operations; an operation can draw from several PFIs."""
    __tablename__ = "pfi_allocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pfi_id = Column(UUID(as_uuid=True), ForeignKey("pfis.id", ondelete="CASCADE"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False)
    quantity_litres = Column(Numeric(14, 2), nullable=False)
    linked_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    pfi = relationship("PFI", back_populates="allocations")
    operation = relationship("Operation", back_populates="pfi_allocations")
    linker = relationship("User", foreign_keys=[linked_by])


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
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True)
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
    # Nullable: Finance can raise ad-hoc invoices outside any operation.
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    bdn_id = Column(UUID(as_uuid=True), ForeignKey("bdns.id"), nullable=True)
    truck_bdn_id = Column(UUID(as_uuid=True), ForeignKey("truck_bdns.id"), nullable=True)
    # Nullable: a standalone invoice may be billed to a manually-entered client
    # who isn't a registered user (client_name/client_email carry it instead).
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    client_name = Column(String(255), nullable=True)
    client_email = Column(String(255), nullable=True)
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
    # Line-item text for standalone invoices (operation-bound ones derive it).
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="invoices")
    bdn = relationship("BDN", back_populates="invoices")
    truck_bdn = relationship("TruckBdn", back_populates="invoices")
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

    category = Column(SAEnum(VoucherCategory, name="vouchercategory"), nullable=False)
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


# ── PFI price sanity ──────────────────────────────────────────────────────
#
# A PFI's amount divided by its litres is the implied price of one litre. On
# 23 of 47 live records that price is impossible: the lowest implies ₦0.39 a
# litre, the highest ₦905,000. Two separate mistakes produce this — an amount
# entered in the wrong unit (or holding a fee rather than the fuel value), and
# a quantity mistyped by three zeros.
#
# It matters because PFIs are the only place the system records what fuel
# costs, so every naira figure derived anywhere — loss valuations included —
# inherits the error.
#
# Warn-only, matching the vessel-ROB guard above it in this codebase: an
# out-of-range figure is nearly always a slip, but blocking the write could
# strand a legitimate correction mid-flow.
#
# The band is deliberately WIDE (₦50–₦20,000). Real marine fuel sits around
# ₦700–₦1,900, so a tighter band would be more useful — but until it is
# settled whether `amount` holds the total contract value or a unit price,
# a narrow band would fire on entries that are perfectly valid under the
# other reading. This band catches both known error patterns and nothing else.
_PRICE_FLOOR = Decimal("50")
_PRICE_CEILING = Decimal("20000")


def _warn_pfi_price_out_of_range(target) -> None:
    amount, litres = target.amount, target.quantity_litres
    if amount is None or litres is None:
        return
    try:
        amt, qty = Decimal(str(amount)), Decimal(str(litres))
    except (InvalidOperation, ValueError):
        return
    if qty <= 0:
        return

    rate = amt / qty
    if rate < _PRICE_FLOOR or rate > _PRICE_CEILING:
        logger.warning(
            "PFI price out of range: %s implies %s per litre "
            "(%s over %s litres) — outside %s-%s, likely a units or "
            "decimal-point error",
            getattr(target, "pfi_number", "?"), round(rate, 2), amt, qty,
            _PRICE_FLOOR, _PRICE_CEILING,
        )


@event.listens_for(PFI, "before_insert")
def _pfi_price_check_on_insert(mapper, connection, target):
    _warn_pfi_price_out_of_range(target)


@event.listens_for(PFI, "before_update")
def _pfi_price_check_on_update(mapper, connection, target):
    _warn_pfi_price_out_of_range(target)
