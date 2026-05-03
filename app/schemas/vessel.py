from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import VesselStatus, RobEntryType


# ── Vessel Schemas ─────────────────────────────────────────────────────────────

class VesselCreate(BaseModel):
    vessel_name: str
    imo_number: Optional[str] = None
    vessel_type: Optional[str] = None
    flag_state: Optional[str] = None
    capacity_mt: Optional[Decimal] = None
    rob_threshold_mt: Optional[Decimal] = None
    current_location: Optional[str] = None

    @field_validator("vessel_name", "imo_number", "vessel_type", "flag_state", "current_location", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class VesselUpdate(BaseModel):
    vessel_name: Optional[str] = None
    imo_number: Optional[str] = None
    vessel_type: Optional[str] = None
    flag_state: Optional[str] = None
    capacity_mt: Optional[Decimal] = None
    rob_threshold_mt: Optional[Decimal] = None
    current_location: Optional[str] = None
    status: Optional[VesselStatus] = None
    is_active: Optional[bool] = None

    @field_validator("vessel_name", "imo_number", "vessel_type", "flag_state", "current_location", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class VesselOut(BaseModel):
    id: UUID
    vessel_name: str
    imo_number: Optional[str] = None
    vessel_type: Optional[str] = None
    flag_state: Optional[str] = None
    capacity_mt: Optional[Decimal] = None
    current_rob_mt: Decimal
    rob_threshold_mt: Decimal
    current_location: Optional[str] = None
    status: VesselStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── ROB Entry Schemas ──────────────────────────────────────────────────────────

class RobEntryCreate(BaseModel):
    entry_type: RobEntryType
    quantity_mt: Decimal
    operation_id: Optional[UUID] = None
    source_description: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("source_description", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("quantity_mt")
    @classmethod
    def validate_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity_mt must be positive (server negates for discharge)")
        return v


class RobEntryOut(BaseModel):
    id: UUID
    vessel_id: UUID
    operation_id: Optional[UUID] = None
    entry_type: RobEntryType
    quantity_mt: Decimal
    rob_before_mt: Decimal
    rob_after_mt: Decimal
    recorded_by: UUID
    source_description: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── ROB Summary ────────────────────────────────────────────────────────────────

class RobChartPoint(BaseModel):
    date: str
    rob_mt: float


class RobSummaryOut(BaseModel):
    vessel_id: UUID
    vessel_name: str
    current_rob_mt: Decimal
    rob_threshold_mt: Decimal
    below_threshold: bool
    recent_entries: List[RobEntryOut]
    chart_data: List[RobChartPoint]
