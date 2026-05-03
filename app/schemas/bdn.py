from typing import Optional, Any
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import BdnStatus


class BdnCreate(BaseModel):
    vessel_id: UUID
    quantity_delivered_mt: Decimal
    product_type: Optional[str] = None
    density: Optional[Decimal] = None
    temperature: Optional[Decimal] = None
    delivery_date: datetime
    notes: Optional[str] = None

    @field_validator("product_type", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("quantity_delivered_mt")
    @classmethod
    def validate_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity_delivered_mt must be positive")
        return v


class BdnOut(BaseModel):
    id: UUID
    bdn_number: str
    operation_id: UUID
    vessel_id: UUID
    generated_by: UUID
    reviewed_by: Optional[UUID] = None
    status: BdnStatus
    quantity_delivered_mt: Decimal
    product_type: Optional[str] = None
    density: Optional[Decimal] = None
    temperature: Optional[Decimal] = None
    delivery_date: datetime
    rejection_reason: Optional[str] = None
    approved_at: Optional[datetime] = None
    pdf_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    version: int
    vessel_name: Optional[str] = None
    generated_by_name: Optional[str] = None

    model_config = {"from_attributes": True}


class BdnApproveRequest(BaseModel):
    notes: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class BdnRejectRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Rejection reason cannot be empty")
        if len(v) < 10:
            raise ValueError("Rejection reason must be at least 10 characters")
        return v
