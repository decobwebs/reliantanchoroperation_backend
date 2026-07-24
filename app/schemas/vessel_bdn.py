from typing import Optional
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import BdnStatus


class VesselBdnCreate(BaseModel):
    """Submitter-facing (Ops Supervisor / Marine Manager), one per vessel
    run. Every field is manually entered — nothing is prefilled. The system
    independently computes its own snapshot of the fields it can derive from
    that specific vessel run for the Bunker Manager to compare against."""
    company_name: str
    product_type: str
    discharge_location: str
    receiving_vessel: str
    quantity_loaded_litres: Decimal
    quantity_discharged_litres: Decimal
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

    @field_validator("company_name", "product_type", "discharge_location", "receiving_vessel", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("company_name", "product_type", "discharge_location", "receiving_vessel")
    @classmethod
    def required_strings(cls, v: str) -> str:
        if not v:
            raise ValueError("This field is required")
        return v

    @field_validator("quantity_loaded_litres", "quantity_discharged_litres", "density", "gov", "gsv", "mt_vacuum", "vcf")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class VesselBdnUpdate(BaseModel):
    """Bunker Manager correction — every submitted field is editable, a
    reason is required. The system_* comparison snapshot is never
    BM-editable — it's a frozen fact of what the system computed."""
    company_name: Optional[str] = None
    product_type: Optional[str] = None
    discharge_location: Optional[str] = None
    receiving_vessel: Optional[str] = None
    quantity_loaded_litres: Optional[Decimal] = None
    quantity_discharged_litres: Optional[Decimal] = None
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

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to edit a Vessel BDN")
        return v


class VesselBdnOut(BaseModel):
    id: UUID
    bdn_number: str
    operation_id: UUID
    vessel_id: UUID
    vessel_activity_id: Optional[UUID] = None
    generated_by: UUID
    generated_by_name: Optional[str] = None
    reviewed_by: Optional[UUID] = None
    status: BdnStatus
    company_name: str
    product_type: str
    discharge_location: str
    receiving_vessel: str
    quantity_loaded_litres: Decimal
    quantity_discharged_litres: Decimal
    variance_litres: Optional[Decimal] = None
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
    system_quantity_loaded_litres: Optional[Decimal] = None
    system_quantity_discharged_litres: Optional[Decimal] = None
    system_discharge_commenced_at: Optional[datetime] = None
    system_discharge_completed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class VesselBdnApprovalProgress(BaseModel):
    """Returned alongside an approval — lets the BM see real progress
    ("N of M vessel runs approved") rather than a false block."""
    bdn: VesselBdnOut
    total_vessel_runs: int
    approved_vessel_runs: int
    operation_completed_gate_cleared: bool


class VesselBdnRejectRequest(BaseModel):
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
