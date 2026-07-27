from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator
from app.models.enums import VesselStage, AuditResult


class VesselActivityCreate(BaseModel):
    vessel_id: UUID
    assigned_to: UUID
    notes: Optional[str] = None


class VesselActivityRecordReceipt(BaseModel):
    truck_delivered_mt: Optional[Decimal] = None   # omit for direct vessel flow
    vessel_received_mt: Decimal
    previous_rob_mt: Decimal
    product_type: Optional[str] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    density: Optional[Decimal] = None
    notes: Optional[str] = None


class VesselActivityRecordBunkering(BaseModel):
    bunkering_start_at: Optional[datetime] = None
    bunkering_end_at: Optional[datetime] = None
    notes: Optional[str] = None


class VesselActivityRecordDischarge(BaseModel):
    quantity_discharged_mt: Decimal
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    notes: Optional[str] = None


class VesselActivityComplete(BaseModel):
    completion_notes: Optional[str] = None


class VesselActivityPatchInitialRob(BaseModel):
    initial_rob_mt: Decimal


# ── Per-vessel stage flow ────────────────────────────────────────────────────

class AdvanceStageRequest(BaseModel):
    stage: VesselStage
    occurred_at: datetime   # always caller-supplied — stages are routinely logged after the fact
    comment: Optional[str] = None

    @field_validator("comment", mode="before")
    @classmethod
    def strip_comment(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class AddCommentRequest(BaseModel):
    stage: Optional[VesselStage] = None
    comment: str

    @field_validator("comment", mode="before")
    @classmethod
    def strip_comment(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("comment")
    @classmethod
    def comment_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Comment cannot be empty")
        return v


class VesselActivityCommentOut(BaseModel):
    id: UUID
    vessel_activity_id: UUID
    stage: Optional[VesselStage] = None
    comment: str
    recorded_by: UUID
    recorded_by_name: Optional[str] = None
    recorded_at: datetime

    model_config = {"from_attributes": True}


class HseChecklistItem(BaseModel):
    item: str
    passed: bool
    notes: Optional[str] = None


class RecordHseRequest(BaseModel):
    checklist: List[HseChecklistItem]
    result: AuditResult
    notes: Optional[str] = None

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


class RecordDischargeQuantitiesRequest(BaseModel):
    gov: Decimal
    vcf: Decimal
    density: Decimal

    @field_validator("gov", "vcf", "density")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


# ── Vessel-only commence -> updates -> complete -> quantities flow ─────────
# Fully separate from the stage flow above and the old ROB-session flow —
# applies only when operation.type == vessel_only (enforced in the service).

class VesselActivityCommenceRequest(BaseModel):
    commenced_user_at: datetime   # the user's own stated commencement time
    description: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class VesselActivityCompleteVesselOpRequest(BaseModel):
    completed_user_at: datetime   # the user's own stated completion time


class AddVesselActivityUpdateRequest(BaseModel):
    content: str
    # image is a separate multipart part handled by the router, not here

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("content")
    @classmethod
    def content_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Content cannot be empty")
        return v


class VesselActivityUpdateOut(BaseModel):
    id: UUID
    vessel_activity_id: UUID
    content: str
    image_url: Optional[str] = None
    recorded_by: UUID
    recorded_by_name: Optional[str] = None
    recorded_at: datetime

    model_config = {"from_attributes": True}


class RecordVesselOperationQuantitiesRequest(BaseModel):
    discharged_quantity_litres: Decimal
    received_quantity_litres: Decimal
    density: Decimal
    temperature_celsius: Decimal
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

    @field_validator("discharged_quantity_litres", "received_quantity_litres", "density", "vcf", "gov")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class VesselActivityCorrectTimingRequest(BaseModel):
    """BM-only correction of any of the four commence/complete timings."""
    commence_system_at: Optional[datetime] = None
    commence_user_at: Optional[datetime] = None
    complete_system_at: Optional[datetime] = None
    complete_user_at: Optional[datetime] = None
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct a vessel-operation timing")
        return v


class VesselActivityOut(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    activity_number: str
    operation_id: UUID
    vessel_id: UUID
    vessel_name: Optional[str] = None
    vessel_current_rob_mt: Optional[Decimal] = None
    assigned_to: UUID
    assigned_by: UUID

    initial_rob_mt: Optional[Decimal] = None
    truck_delivered_mt: Optional[Decimal] = None
    vessel_received_mt: Optional[Decimal] = None
    variance_mt: Optional[Decimal] = None
    previous_rob_mt: Optional[Decimal] = None
    new_rob_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    final_rob_mt: Optional[Decimal] = None

    product_type: Optional[str] = None
    temperature_celsius: Optional[Decimal] = None
    density: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None

    bunkering_start_at: Optional[datetime] = None
    bunkering_end_at: Optional[datetime] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None

    status: str
    notes: Optional[str] = None
    completion_notes: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    # ── Stage flow ──
    stage: Optional[VesselStage] = None
    stage_cast_off_at: Optional[datetime] = None
    stage_outbound_at: Optional[datetime] = None
    stage_alongside_at: Optional[datetime] = None
    stage_hse_check_at: Optional[datetime] = None
    stage_discharging_at: Optional[datetime] = None
    stage_discharge_completed_at: Optional[datetime] = None

    # ── HSE ──
    hse_checklist: List[HseChecklistItem] = []
    hse_result: Optional[AuditResult] = None
    hse_conducted_by: Optional[UUID] = None
    hse_conducted_at: Optional[datetime] = None
    hse_notes: Optional[str] = None

    # ── Discharge arithmetic — reused by both the stage flow (full_operation)
    # and the commence/complete flow (vessel_only) ──
    gov: Optional[Decimal] = None
    vcf: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None

    # ── Vessel-only commence/complete flow ──
    commence_system_at: Optional[datetime] = None
    commence_user_at: Optional[datetime] = None
    commence_description: Optional[str] = None
    complete_system_at: Optional[datetime] = None
    complete_user_at: Optional[datetime] = None
    discharged_quantity_litres: Optional[Decimal] = None
    received_quantity_litres: Optional[Decimal] = None
    quantity_recorded_at: Optional[datetime] = None
    quantity_description: Optional[str] = None

    comments: List[VesselActivityCommentOut] = []
    updates: List[VesselActivityUpdateOut] = []
