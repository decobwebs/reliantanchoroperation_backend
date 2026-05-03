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


class InvoiceSendRequest(BaseModel):
    """Mark invoice as sent (optionally attach a PDF URL)."""
    pdf_url: Optional[str] = None
    notes: Optional[str] = None


class InvoiceMarkPaidRequest(BaseModel):
    notes: Optional[str] = None


class InvoiceOut(BaseModel):
    id: UUID
    invoice_number: str
    operation_id: UUID
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
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
