from typing import Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator


# ── Standalone PFI creation (FM or BM — before operation exists) ───────────────

class StandalonePfiCreate(BaseModel):
    """Create a PFI before an operation exists. operation_id is set later."""
    amount: Decimal
    currency: str = "NGN"
    exchange_rate: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    client_ref: Optional[str] = None     # client's own reference number
    document_url: Optional[str] = None   # scanned/uploaded PFI doc

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("supplier_name", "description", "client_ref", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero")
        return v


class PfiConfirmPaymentRequest(BaseModel):
    """FM confirms that payment for this PFI has been received/made."""
    receipt_url: Optional[str] = None
    notes: Optional[str] = None


# ── Operation-scoped PFI creation (legacy — BM links PFI to existing operation) ─

class PfiCreate(BaseModel):
    amount: Decimal
    currency: str = "NGN"
    exchange_rate: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    document_url: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("supplier_name", "description", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero")
        return v


class PfiGenerateRequest(BaseModel):
    """Inputs for system-generated PFI PDF. Operation data is pulled server-side."""
    rate_per_mt: Decimal
    validity_days: int = 7
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    tax_rate: Decimal = Decimal("0")
    exchange_rate: Optional[Decimal] = None
    notes: Optional[str] = None

    @field_validator("rate_per_mt")
    @classmethod
    def positive_rate(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Rate per MT must be greater than zero")
        return v

    @field_validator("tax_rate")
    @classmethod
    def valid_tax(cls, v: Decimal) -> Decimal:
        if v < 0 or v > 100:
            raise ValueError("Tax rate must be between 0 and 100")
        return v

    @field_validator("validity_days")
    @classmethod
    def positive_validity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Validity must be at least 1 day")
        return v

    @field_validator("supplier_name", "description", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class PfiOut(BaseModel):
    id: UUID
    pfi_number: str
    operation_id: Optional[UUID] = None   # null until linked to operation
    linked_by: UUID
    amount: Decimal
    currency: str
    exchange_rate: Optional[Decimal] = None
    amount_ngn: Optional[Decimal] = None
    supplier_name: Optional[str] = None
    description: Optional[str] = None
    document_url: Optional[str] = None
    receipt_url: Optional[str] = None
    client_ref: Optional[str] = None
    confirmed_by: Optional[UUID] = None
    confirmed_at: Optional[datetime] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Payment Schemas ────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    pfi_id: UUID
    amount: Decimal
    currency: str = "NGN"
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    payment_date: datetime
    voucher_url: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("payment_method", "payment_reference", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be greater than zero")
        return v


class PaymentConfirmRequest(BaseModel):
    notes: Optional[str] = None


class PaymentOut(BaseModel):
    id: UUID
    pfi_id: UUID
    operation_id: UUID
    processed_by: UUID
    amount: Decimal
    currency: str
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    payment_date: datetime
    voucher_number: str
    voucher_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
