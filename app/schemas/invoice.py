from datetime import datetime, date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class InvoiceCreate(BaseModel):
    bdn_id: Optional[UUID] = None   # required for vessel/full ops; omit for truck_only
    amount: Decimal
    currency: str = "USD"
    exchange_rate: Optional[Decimal] = None
    tax_amount: Decimal = Decimal("0")
    due_date: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.upper().strip()


class StandaloneInvoiceCreate(BaseModel):
    """Create an invoice with no operation (ad-hoc Finance billing).

    Unlike InvoiceCreate, client_id cannot be derived from an operation and the
    PDF line item cannot be built from operation type/product/route — so both are
    supplied explicitly here.
    """
    client_id: UUID
    description: str                     # line item text on the PDF
    amount: Decimal
    currency: str = "USD"
    exchange_rate: Optional[Decimal] = None
    tax_amount: Decimal = Decimal("0")
    due_date: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("description", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("description")
    @classmethod
    def description_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Description is required for a standalone invoice")
        return v

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero")
        return v

    @field_validator("tax_amount")
    @classmethod
    def non_negative_tax(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Tax amount cannot be negative")
        return v


class InvoiceSendRequest(BaseModel):
    """Mark invoice as sent (optionally attach a PDF URL)."""
    pdf_url: Optional[str] = None
    notes: Optional[str] = None


class InvoiceMarkPaidRequest(BaseModel):
    notes: Optional[str] = None


class InvoiceOut(BaseModel):
    id: UUID
    invoice_number: str
    operation_id: Optional[UUID] = None   # null for standalone (ad-hoc) invoices
    bdn_id: Optional[UUID] = None
    client_id: UUID
    generated_by: UUID
    amount: Decimal
    currency: str
    exchange_rate: Optional[Decimal] = None
    tax_amount: Decimal
    total_amount: Decimal
    due_date: Optional[date] = None
    status: str
    pdf_url: Optional[str] = None
    sent_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    # Reconciliation — populated by the router, not stored on the model
    advance_paid: Optional[Decimal] = None   # sum of advance payments received via PFI
    balance_due: Optional[Decimal] = None    # total_amount - advance_paid

    model_config = {"from_attributes": True}
