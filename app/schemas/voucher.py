from typing import Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import VoucherCategory, VoucherStatus


class VoucherCreate(BaseModel):
    category: VoucherCategory
    amount: Decimal
    currency: str = "NGN"
    exchange_rate: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    payment_date: Optional[datetime] = None
    pfi_id: Optional[UUID] = None
    notes: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("supplier_name", "description", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero")
        return v


class VoucherApproveRequest(BaseModel):
    notes: Optional[str] = None


class VoucherRejectRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rejection reason cannot be empty")
        return v.strip()


class VoucherAttachReceiptRequest(BaseModel):
    receipt_url: str


class VoucherOut(BaseModel):
    id: UUID
    voucher_number: str
    operation_id: Optional[UUID] = None
    pfi_id: Optional[UUID] = None
    recorded_by: UUID
    approved_by: Optional[UUID] = None
    category: VoucherCategory
    amount: Decimal
    currency: str
    exchange_rate: Optional[Decimal] = None
    amount_ngn: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    receipt_url: Optional[str] = None
    notes: Optional[str] = None
    status: VoucherStatus
    payment_date: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
