from typing import Optional
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import BdnStatus


class TruckBdnCreate(BaseModel):
    """Submitter-facing (Ops Supervisor / Logistics Officer). Every field is
    manually entered — nothing is prefilled from the operation's trucks. The
    system independently computes its own snapshot of the fields it can
    derive (product type, discharge location, quantities, discharge timing)
    for the Bunker Manager to compare against what was submitted here."""
    company_name: str
    product_type: str
    discharge_location: str
    quantity_loaded_mt: Decimal
    quantity_discharged_mt: Decimal
    density: Decimal
    temperature_before_loading: Decimal
    temperature_after_loading: Decimal
    vcf: Decimal
    gov: Decimal
    gsv: Decimal
    mt_vacuum: Decimal
    discharge_commenced_at: datetime
    discharge_completed_at: datetime
    discharge_completion_date: date
    notes: Optional[str] = None

    @field_validator("company_name", "product_type", "discharge_location", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("company_name", "product_type", "discharge_location")
    @classmethod
    def required_strings(cls, v: str) -> str:
        if not v:
            raise ValueError("This field is required")
        return v

    @field_validator("quantity_loaded_mt", "quantity_discharged_mt", "density", "gov", "gsv", "mt_vacuum")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v

    @field_validator("vcf")
    @classmethod
    def positive_vcf(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("VCF must be greater than zero")
        return v


class TruckBdnUpdate(BaseModel):
    """Bunker Manager correction — every submitted field is editable, a
    reason is required. Mirrors TruckWaiverUpdate's edit-audit-trail shape.
    The system_* comparison snapshot is never BM-editable — it's a frozen
    fact of what the system computed at submission time."""
    company_name: Optional[str] = None
    product_type: Optional[str] = None
    discharge_location: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    density: Optional[Decimal] = None
    temperature_before_loading: Optional[Decimal] = None
    temperature_after_loading: Optional[Decimal] = None
    vcf: Optional[Decimal] = None
    gov: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None
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
    product_type: str
    discharge_location: str
    quantity_loaded_mt: Decimal
    quantity_discharged_mt: Decimal
    variance_mt: Optional[Decimal] = None
    density: Decimal
    temperature_before_loading: Decimal
    temperature_after_loading: Decimal
    vcf: Decimal
    gov: Decimal
    gsv: Decimal
    mt_vacuum: Decimal
    discharge_commenced_at: datetime
    discharge_completed_at: datetime
    discharge_completion_date: date
    system_product_type: Optional[str] = None
    system_discharge_location: Optional[str] = None
    system_quantity_loaded_mt: Optional[Decimal] = None
    system_quantity_discharged_mt: Optional[Decimal] = None
    system_discharge_commenced_at: Optional[datetime] = None
    system_discharge_completed_at: Optional[datetime] = None
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
