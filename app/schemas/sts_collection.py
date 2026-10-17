from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.enums import AuditResult

StsStage = Literal["cast_off", "alongside", "receiving_started", "receiving_completed"]
StsHsePhase = Literal["pre", "during", "post"]


def _strip(v: Optional[str]) -> Optional[str]:
    return v.strip() if isinstance(v, str) else v


def _reason_required(v: str) -> str:
    if not v or len(v) < 5:
        raise ValueError("A reason of at least 5 characters is required")
    return v


class StsCollectionCreate(BaseModel):
    source_vessel_name: str
    source_vessel_imo: Optional[str] = None
    product_type: Optional[str] = None
    location: Optional[str] = None
    # Defaults to the operation's own barge; only needed when it has none.
    vessel_id: Optional[UUID] = None
    notes: Optional[str] = None

    @field_validator("source_vessel_name", "source_vessel_imo", "product_type", "location", "notes", mode="before")
    @classmethod
    def strip_text(cls, v: Optional[str]) -> Optional[str]:
        return _strip(v)

    @field_validator("source_vessel_name")
    @classmethod
    def name_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Source vessel name is required")
        return v


class StsCollectionDetailsUpdate(BaseModel):
    """Who/where details — editable while the collection is live."""
    source_vessel_name: Optional[str] = None
    source_vessel_imo: Optional[str] = None
    product_type: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("source_vessel_name", "source_vessel_imo", "product_type", "location", "notes", mode="before")
    @classmethod
    def strip_text(cls, v: Optional[str]) -> Optional[str]:
        return _strip(v)


class StsStageAdvance(BaseModel):
    stage: StsStage
    # When it actually happened — staff often record after the fact.
    occurred_at: Optional[datetime] = None


class StsHseItem(BaseModel):
    section: Optional[str] = None
    item: str
    passed: bool
    notes: Optional[str] = None


class StsHseRecord(BaseModel):
    phase: StsHsePhase
    checklist: List[StsHseItem]
    result: AuditResult
    notes: Optional[str] = None
    safety_officer: Optional[str] = None
    # Required only when overwriting an already-recorded phase.
    reason: Optional[str] = None


class StsFigures(BaseModel):
    """The receipt BDN figures. GOV/GSV/MT(vac)/density/VCF are required —
    MT(vac) is what credits the barge; temperature is optional."""
    gov: Decimal
    gsv: Decimal
    mt_vacuum: Decimal
    density: Decimal
    vcf: Decimal
    temperature: Optional[Decimal] = None

    @field_validator("gov", "gsv", "mt_vacuum", "density", "vcf")
    @classmethod
    def positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class StsFiguresCorrection(StsFigures):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return _strip(v)

    @field_validator("reason")
    @classmethod
    def reason_ok(cls, v: str) -> str:
        return _reason_required(v)


class StsReasonRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return _strip(v)

    @field_validator("reason")
    @classmethod
    def reason_ok(cls, v: str) -> str:
        return _reason_required(v)


class StsCollectionOut(BaseModel):
    id: UUID
    operation_id: UUID
    vessel_id: UUID
    vessel_name: Optional[str] = None
    source_vessel_name: str
    source_vessel_imo: Optional[str] = None
    product_type: Optional[str] = None
    location: Optional[str] = None
    status: str
    stage: Optional[str] = None
    cast_off_at: Optional[datetime] = None
    alongside_at: Optional[datetime] = None
    receiving_started_at: Optional[datetime] = None
    receiving_completed_at: Optional[datetime] = None
    hse: Dict[str, Any] = {}
    receipt_number: Optional[str] = None
    gov: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None
    density: Optional[Decimal] = None
    vcf: Optional[Decimal] = None
    temperature: Optional[Decimal] = None
    submitted_by: Optional[UUID] = None
    submitted_by_name: Optional[str] = None
    submitted_at: Optional[datetime] = None
    reviewed_by: Optional[UUID] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    notes: Optional[str] = None
    created_by: UUID
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # True when this was recorded after the barge had already started
    # delivering — the ROB snapshot taken at that point won't include it.
    recorded_after_delivery_started: bool = False

    model_config = {"from_attributes": True}
