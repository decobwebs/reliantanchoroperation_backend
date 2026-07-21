from typing import Optional
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import BdnStatus


class TruckBdnCreate(BaseModel):
    """Submitter-facing (Ops Supervisor / Logistics Officer). Everything else
    on the BDN — product type, discharge location, quantities, timestamps —
    is computed server-side from the operation's trucks; the submitter only
    provides these two fields."""
    company_name: str
    notes: Optional[str] = None

    @field_validator("company_name", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("company_name")
    @classmethod
    def company_name_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Company name is required")
        return v


class TruckBdnUpdate(BaseModel):
    """Bunker Manager correction — every field is editable, a reason is
    required. Mirrors TruckWaiverUpdate's edit-audit-trail shape."""
    company_name: Optional[str] = None
    product_type: Optional[str] = None
    discharge_location: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    discharge_commenced_at: Optional[datetime] = None
    discharge_completed_at: Optional[datetime] = None
    discharge_completion_date: Optional[date] = None
    notes: Optional[str] = None
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        v = v.strip() if v else v
        if not v:
            raise ValueError("A reason is required to edit a Truck BDN")
        return v


class TruckBdnOut(BaseModel):
    id: UUID
    truck_bdn_number: str
    operation_id: UUID
    generated_by: UUID
    reviewed_by: Optional[UUID] = None
    status: BdnStatus
    company_name: str
    product_type: Optional[str] = None
    discharge_location: Optional[str] = None
    quantity_loaded_mt: Decimal
    quantity_discharged_mt: Decimal
    variance_mt: Optional[Decimal] = None
    discharge_commenced_at: Optional[datetime] = None
    discharge_completed_at: Optional[datetime] = None
    discharge_completion_date: Optional[date] = None
    rejection_reason: Optional[str] = None
    approved_at: Optional[datetime] = None
    pdf_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    generated_by_name: Optional[str] = None

    model_config = {"from_attributes": True}


class TruckBdnApproveRequest(BaseModel):
    notes: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckBdnRejectRequest(BaseModel):
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
