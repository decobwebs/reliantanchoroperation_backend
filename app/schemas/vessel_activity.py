import re
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator
from app.models.enums import VesselStage, AuditResult

# Deliberately permissive — this only catches typos like a missing "@" before a
# send is queued. Address validity is proven by delivery, not by a regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct the initial ROB")
        return v


# ── Per-vessel stage flow ────────────────────────────────────────────────────

class AdvanceStageRequest(BaseModel):
    stage: VesselStage
    occurred_at: datetime   # always caller-supplied — stages are routinely logged after the fact
    comment: Optional[str] = None

    @field_validator("comment", mode="before")
    @classmethod
    def strip_comment(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class SetCastOffContactsRequest(BaseModel):
    """The client block captured at Cast Off — who the run is for, and who
    should hear about it. Editable afterwards by the BM, so this is a plain
    upsert with no stage gate: recording a detail late must never be harder
    than recording it on time."""
    client_name: Optional[str] = None
    client_vessel_name: Optional[str] = None
    emails: List[str] = []

    @field_validator("client_name", "client_vessel_name", mode="before")
    @classmethod
    def strip_names(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("emails", mode="before")
    @classmethod
    def clean_emails(cls, v: Optional[List[str]]) -> List[str]:
        """Trim, drop blanks, and de-duplicate case-insensitively while keeping
        the order the BM entered them. These get merged with the Naval Clearance
        recipients later, so a duplicate here would become a duplicate send."""
        if not v:
            return []
        seen, out = set(), []
        for raw in v:
            e = (raw or "").strip()
            if not e or e.lower() in seen:
                continue
            seen.add(e.lower())
            out.append(e)
        return out

    @field_validator("emails")
    @classmethod
    def valid_emails(cls, v: List[str]) -> List[str]:
        bad = [e for e in v if not _EMAIL_RE.match(e)]
        if bad:
            raise ValueError(f"Not a valid email address: {', '.join(bad)}")
        return v


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
    edited_at: Optional[datetime] = None
    edited_by: Optional[UUID] = None
    edited_by_name: Optional[str] = None
    edit_reason: Optional[str] = None

    model_config = {"from_attributes": True}


# The three HSE checks per vessel run (migration 059). "pre" maps to the
# original unprefixed hse_* columns — see VesselActivity's comment for why.
HsePhase = Literal["pre", "during", "post"]


class HseChecklistItem(BaseModel):
    # `section` is stored with each item so a completed checklist stays
    # self-describing — if the template's grouping is later changed, an
    # already-signed-off record still reads the way it was signed.
    section: Optional[str] = None
    item: str
    passed: bool
    notes: Optional[str] = None


class RecordHseRequest(BaseModel):
    checklist: List[HseChecklistItem]
    result: AuditResult
    notes: Optional[str] = None
    safety_officer: Optional[str] = None
    # Required only when overwriting an already-recorded checklist (a BM
    # correction) — enforced in the service, which alone can see prior state.
    reason: Optional[str] = None
    # Which of the three checks this is (migration 059). Defaults to "pre" so
    # any caller written before the split keeps hitting the same columns it
    # always did, rather than silently starting a new empty record.
    phase: HsePhase = "pre"

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
    description: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


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


class EditVesselActivityUpdateRequest(BaseModel):
    """BM-only correction of a posted update. Records are never deleted —
    only corrected, with the edit marked on the row itself."""
    content: Optional[str] = None
    reason: str
    # a replacement image is a separate multipart part handled by the router

    @field_validator("content", "reason", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct an update")
        return v


class EditVesselActivityCommentRequest(BaseModel):
    """BM-only correction of a posted comment."""
    comment: str
    reason: str

    @field_validator("comment", "reason", mode="before")
    @classmethod
    def strip_strings(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("comment")
    @classmethod
    def comment_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Comment cannot be empty")
        return v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to correct a comment")
        return v


class UncancelRequest(BaseModel):
    """Restoring a cancelled activity or receiving-vessel leg."""
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to restore a cancelled record")
        return v


class VesselActivityUpdateOut(BaseModel):
    id: UUID
    vessel_activity_id: UUID
    leg_id: Optional[UUID] = None
    content: str
    image_url: Optional[str] = None
    recorded_by: UUID
    recorded_by_name: Optional[str] = None
    recorded_at: datetime
    edited_at: Optional[datetime] = None
    edited_by: Optional[UUID] = None
    edited_by_name: Optional[str] = None
    edit_reason: Optional[str] = None

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


class RecordLoadingReceiptRequest(BaseModel):
    """One-time Received Quantity + quality readings at the loading step —
    part of the six-stage + multiple-receiving-vessel-legs flow. Loading
    happens once per barge run; per-leg discharge readings are recorded
    separately (see app/schemas/vessel_activity_leg.py)."""
    received_quantity_litres: Decimal
    density: Decimal
    temperature: Decimal
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

    @field_validator("received_quantity_litres", "density", "temperature", "vcf", "gov")
    @classmethod
    def positive_values(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class VesselActivityCorrectTimingRequest(BaseModel):
    """BM-only correction of the loading record — both timestamp pairs, both
    descriptions, and the activity notes. Every field is optional; only what
    is sent is changed. `reason` is always required and audit-logged."""
    commence_system_at: Optional[datetime] = None
    commence_user_at: Optional[datetime] = None
    commence_description: Optional[str] = None
    complete_system_at: Optional[datetime] = None
    complete_user_at: Optional[datetime] = None
    complete_description: Optional[str] = None
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
    stage_approach_at: Optional[datetime] = None
    stage_alongside_at: Optional[datetime] = None
    stage_hse_check_at: Optional[datetime] = None
    stage_commence_discharge_at: Optional[datetime] = None
    stage_discharge_completed_at: Optional[datetime] = None

    # ── HSE — three checks per run; the unprefixed set is the PRE check ──
    hse_checklist: List[HseChecklistItem] = []
    hse_result: Optional[AuditResult] = None
    hse_conducted_by: Optional[UUID] = None
    hse_conducted_at: Optional[datetime] = None
    hse_notes: Optional[str] = None
    hse_safety_officer: Optional[str] = None

    hse_during_checklist: List[HseChecklistItem] = []
    hse_during_result: Optional[AuditResult] = None
    hse_during_conducted_by: Optional[UUID] = None
    hse_during_conducted_at: Optional[datetime] = None
    hse_during_notes: Optional[str] = None
    hse_during_safety_officer: Optional[str] = None

    hse_post_checklist: List[HseChecklistItem] = []
    hse_post_result: Optional[AuditResult] = None
    hse_post_conducted_by: Optional[UUID] = None
    hse_post_conducted_at: Optional[datetime] = None
    hse_post_notes: Optional[str] = None
    hse_post_safety_officer: Optional[str] = None

    # ── Cast Off client block ──
    cast_off_client_name: Optional[str] = None
    cast_off_client_vessel_name: Optional[str] = None
    cast_off_client_emails: List[str] = []

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
    complete_description: Optional[str] = None
    discharged_quantity_litres: Optional[Decimal] = None
    received_quantity_litres: Optional[Decimal] = None
    quantity_recorded_at: Optional[datetime] = None
    quantity_description: Optional[str] = None

    # ── Loading Received Quantity — one-time, six-stage + legs flow ──
    loading_received_quantity_litres: Optional[Decimal] = None
    loading_density: Optional[Decimal] = None
    loading_temperature: Optional[Decimal] = None
    loading_vcf: Optional[Decimal] = None
    loading_gov: Optional[Decimal] = None
    loading_gsv: Optional[Decimal] = None
    loading_mt_vacuum: Optional[Decimal] = None
    loading_quantity_recorded_at: Optional[datetime] = None
    loading_quantity_description: Optional[str] = None

    comments: List[VesselActivityCommentOut] = []
    updates: List[VesselActivityUpdateOut] = []
    legs: List["VesselActivityLegOut"] = []


# Resolves the "VesselActivityLegOut" forward ref above. Imported at the
# bottom (not the top) because vessel_activity_leg.py imports
# HseChecklistItem from this module — a top-of-file import here would be
# circular. By this point HseChecklistItem already exists in this
# module's namespace, so the circular import resolves cleanly.
from app.schemas.vessel_activity_leg import VesselActivityLegOut  # noqa: E402
VesselActivityOut.model_rebuild()
