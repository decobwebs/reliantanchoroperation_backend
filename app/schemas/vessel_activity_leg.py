from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator
from app.models.enums import VesselLegStage, AuditResult
from app.schemas.vessel_activity import HseChecklistItem


class VesselActivityLegCreate(BaseModel):
    receiving_vessel_name: str
    imo_number: Optional[str] = None
    eta_at: Optional[datetime] = None

    @field_validator("receiving_vessel_name", "imo_number", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("receiving_vessel_name")
    @classmethod
    def name_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Receiving vessel name is required")
        return v


class AdvanceLegStageRequest(BaseModel):
    stage: VesselLegStage
    occurred_at: datetime   # the user's own stated time — server captures now() separately

    @field_validator("occurred_at")
    @classmethod
    def not_none(cls, v: datetime) -> datetime:
        return v


class RecordLegHseRequest(BaseModel):
    checklist: List[HseChecklistItem]
    result: AuditResult
    notes: Optional[str] = None
    safety_officer: Optional[str] = None
    # Required only when overwriting an already-recorded checklist (a BM
    # correction) — enforced in the service, which alone can see prior state.
    reason: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("checklist")
    @classmethod
    def at_least_one_item(cls, v: List[HseChecklistItem]) -> List[HseChecklistItem]:
        if not v:
            raise ValueError("At least one checklist item is required")
        return v


class RecordLegQuantitiesRequest(BaseModel):
    quantity_discharged_litres: Decimal
    density: Decimal
    temperature_before_loading: Decimal
    temperature_after_loading: Decimal
    vcf: Decimal
    gov: Decimal
    description: Optional[str] = None
    # Required only on a resubmission (BM correction) — enforced in the
    # service, not here, since the schema alone can't see prior state.
    reason: Optional[str] = None

    @field_validator("description", "reason", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("quantity_discharged_litres", "density", "temperature_before_loading", "temperature_after_loading", "vcf", "gov")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class EditVesselActivityLegRequest(BaseModel):
    """BM-only correction of a receiving vessel's identity details."""
    receiving_vessel_name: Optional[str] = None
    imo_number: Optional[str] = None
    eta_at: Optional[datetime] = None
    reason: str

    @field_validator("receiving_vessel_name", "imo_number", "reason", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("receiving_vessel_name")
    @classmethod
    def name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v:
            raise ValueError("Receiving vessel name cannot be blank")
        return v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct a receiving vessel")
        return v


class CorrectLegTimingRequest(BaseModel):
    """BM-only correction of the eight leg stage timestamps, and optionally
    the leg's own stage — a rollback for a stage logged in error."""
    stage: Optional[VesselLegStage] = None
    stage_cast_off_system_at: Optional[datetime] = None
    stage_cast_off_user_at: Optional[datetime] = None
    stage_alongside_system_at: Optional[datetime] = None
    stage_alongside_user_at: Optional[datetime] = None
    stage_discharge_commenced_system_at: Optional[datetime] = None
    stage_discharge_commenced_user_at: Optional[datetime] = None
    stage_discharge_completed_system_at: Optional[datetime] = None
    stage_discharge_completed_user_at: Optional[datetime] = None
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct a leg timing")
        return v


class CancelLegRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to cancel a receiving-vessel leg")
        return v


class VesselActivityLegOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    vessel_activity_id: UUID
    receiving_vessel_name: str
    imo_number: Optional[str] = None
    eta_at: Optional[datetime] = None
    created_by: UUID
    created_at: datetime

    stage: Optional[VesselLegStage] = None
    stage_cast_off_system_at: Optional[datetime] = None
    stage_cast_off_user_at: Optional[datetime] = None
    stage_alongside_system_at: Optional[datetime] = None
    stage_alongside_user_at: Optional[datetime] = None
    stage_discharge_commenced_system_at: Optional[datetime] = None
    stage_discharge_commenced_user_at: Optional[datetime] = None
    stage_discharge_completed_system_at: Optional[datetime] = None
    stage_discharge_completed_user_at: Optional[datetime] = None

    hse_checklist: List[HseChecklistItem] = []
    hse_result: Optional[AuditResult] = None
    hse_conducted_by: Optional[UUID] = None
    hse_conducted_at: Optional[datetime] = None
    hse_notes: Optional[str] = None
    hse_safety_officer: Optional[str] = None

    quantity_discharged_litres: Optional[Decimal] = None
    density: Optional[Decimal] = None
    temperature_before_loading: Optional[Decimal] = None
    temperature_after_loading: Optional[Decimal] = None
    vcf: Optional[Decimal] = None
    gov: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None
    quantity_recorded_at: Optional[datetime] = None
    quantity_description: Optional[str] = None

    cancelled_at: Optional[datetime] = None
    cancelled_reason: Optional[str] = None
