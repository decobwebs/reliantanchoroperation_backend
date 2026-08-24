from typing import Optional, List, Any, Dict
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator, model_validator
from app.models.enums import (
    OperationType, OperationStatus, TaskType, TaskStatus, Priority, ProductType, VesselSourceType
)


# ── Inline task assignment (one-step operation creation) ──────────────────────

class InlineTaskAssignment(BaseModel):
    assigned_to: UUID
    task_type: TaskType
    priority: Priority = Priority.normal
    instructions: Optional[str] = None
    due_date: Optional[datetime] = None

    @field_validator("instructions", mode="before")
    @classmethod
    def strip_instructions(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


# ── Operation Schemas ──────────────────────────────────────────────────────────

class OperationProductCreate(BaseModel):
    product_type: ProductType
    quantity_mt: Decimal

    @field_validator("quantity_mt")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class OperationPfiAllocationCreate(BaseModel):
    pfi_id: UUID
    quantity_litres: Decimal

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class CreateOperationRequest(BaseModel):
    type: OperationType
    # Vessel-only only — hard-required for that type, must be absent otherwise.
    # A pure label (Truck vs Terminal source) — never gates any truck UI.
    source_type: Optional[VesselSourceType] = None
    # BM can create an operation before picking a client — fill it in later.
    client_id: Optional[UUID] = None
    products: List[OperationProductCreate]
    currency: str = "NGN"
    vessel_id: Optional[UUID] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    notes: Optional[str] = None
    # BM selects existing paid/standalone PFIs and how much of each to draw
    # down — PFIs can only ever be linked at operation creation from here on.
    pfi_allocations: Optional[List[OperationPfiAllocationCreate]] = None
    # One-step: include task assignments to auto-advance past draft
    assignments: Optional[List[InlineTaskAssignment]] = None

    @field_validator("products")
    @classmethod
    def at_least_one_product(cls, v: List[OperationProductCreate]) -> List[OperationProductCreate]:
        if not v:
            raise ValueError("At least one product is required")
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @model_validator(mode="after")
    def source_type_matches_operation_type(self):
        if self.type == OperationType.vessel_only and self.source_type is None:
            raise ValueError("source_type is required for vessel-only operations")
        if self.type != OperationType.vessel_only and self.source_type is not None:
            raise ValueError("source_type only applies to vessel-only operations")
        return self


OPERATION_COLORS = [
    "red", "orange", "amber", "green", "teal", "blue", "indigo", "purple", "pink", "gray",
]


class LinkNavalClearanceRequest(BaseModel):
    naval_clearance_id: UUID


class UnlinkNavalClearanceRequest(BaseModel):
    naval_clearance_id: UUID
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to unlink a Naval Clearance")
        return v


class SetOperationColorRequest(BaseModel):
    color: Optional[str] = None

    @field_validator("color")
    @classmethod
    def valid_color(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in OPERATION_COLORS:
            raise ValueError(f"Color must be one of: {', '.join(OPERATION_COLORS)}")
        return v


class UpdateOperationRequest(BaseModel):
    # Type decides which pipeline the operation follows, so it is only
    # accepted while the operation is still in draft — enforced in the
    # service, which alone can see the current status. Switching it later
    # would leave an operation sitting in a status its new pipeline has no
    # route out of.
    type: Optional[OperationType] = None
    # Full replacement of the product lines when supplied; omitted leaves
    # them untouched. Same shape Create uses.
    products: Optional[List[OperationProductCreate]] = None
    client_id: Optional[UUID] = None
    actual_volume_mt: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    vessel_id: Optional[UUID] = None
    source_type: Optional[VesselSourceType] = None
    reason: Optional[str] = None  # why this edit was made — surfaced in the Activity tab


class TransitionRequest(BaseModel):
    to_status: OperationStatus
    reason: Optional[str] = None
    completion_notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CloseOperationRequest(BaseModel):
    """Transitions to completed, optionally capturing a ROB close-out for
    operations with a vessel. actual_rob_mt is the BM's physical reading —
    left blank if this operation has no vessel or the BM isn't ready to
    record it yet (the transition itself still goes through)."""
    actual_rob_mt: Optional[Decimal] = None
    completion_notes: Optional[str] = None
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to close an operation")
        return v


class PauseRequest(BaseModel):
    reason: str


class ResumeRequest(BaseModel):
    reason: Optional[str] = None


class ReopenRequest(BaseModel):
    """Create a new version of a completed/archived operation."""
    version_notes: str

    @field_validator("version_notes", mode="before")
    @classmethod
    def strip_notes(cls, v: str) -> str:
        return v.strip()


# ── Output Schemas ─────────────────────────────────────────────────────────────

class OperationProductOut(BaseModel):
    id: UUID
    operation_id: UUID
    product_type: str
    quantity_mt: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class OperationNavalClearanceSummary(BaseModel):
    id: UUID
    clearance_number: str
    ppdl_number: Optional[str] = None
    bfl_numbers: List[str] = []
    products: List[str] = []
    is_valid: bool = True

    model_config = {"from_attributes": True}


class OperationOut(BaseModel):
    id: UUID
    operation_number: str
    type: OperationType
    source_type: Optional[VesselSourceType] = None
    status: OperationStatus
    products: List[OperationProductOut] = []
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    client_id: Optional[UUID] = None
    created_by: UUID
    actual_volume_mt: Optional[Decimal] = None
    notes: Optional[str] = None
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    completed_at: Optional[datetime] = None
    completion_notes: Optional[str] = None
    closed_at: Optional[datetime] = None
    expected_rob_mt: Optional[Decimal] = None
    actual_rob_mt: Optional[Decimal] = None
    rob_closed_by: Optional[UUID] = None
    currency: str
    vessel_id: Optional[UUID] = None
    naval_clearance_id: Optional[UUID] = None
    naval_clearance: Optional[OperationNavalClearanceSummary] = None
    # Supersedes naval_clearance_id/naval_clearance above (kept, unused
    # going forward) — an operation can hold any number of clearances now.
    naval_clearances: List[OperationNavalClearanceSummary] = []
    color: Optional[str] = None
    trucks_required: Optional[int] = None
    version: int = 1
    parent_operation_id: Optional[UUID] = None
    version_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("naval_clearances", mode="before")
    @classmethod
    def unwrap_naval_clearance_links(cls, v: Any) -> Any:
        """v is Operation.naval_clearances — a list of OperationNavalClearance
        join rows. Each summary is validated off the actual NavalClearance
        each row points to, not the join row itself."""
        if not v:
            return []
        return [link.naval_clearance for link in v if getattr(link, "naval_clearance", None)]


class OperationTotalsOut(BaseModel):
    """The six BM totals for the Marine tab — each an independent figure,
    never combined into a forced reconciliation (decision 12)."""
    total_loaded_mt: Decimal
    total_discharged_mt: Decimal
    total_received_mt: Decimal
    vessels_received: int
    tts_variance_mt: Decimal
    sts_variance_mt: Decimal


class OperationDetailOut(OperationOut):
    client: Optional[Any] = None
    creator: Optional[Any] = None
    status_history: List[Any] = []
    task_assignments: List[Any] = []

    model_config = {"from_attributes": True}


# ── Status History Schemas ─────────────────────────────────────────────────────

class StatusHistoryOut(BaseModel):
    id: UUID
    operation_id: UUID
    from_status: Optional[OperationStatus] = None
    to_status: OperationStatus
    changed_by: UUID
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Task Assignment Schemas ────────────────────────────────────────────────────

class CreateTaskAssignmentRequest(BaseModel):
    operation_id: UUID
    assigned_to: UUID
    task_type: TaskType
    priority: Priority = Priority.normal
    due_date: Optional[datetime] = None
    instructions: Optional[str] = None


class TaskAssignmentOut(BaseModel):
    id: UUID
    operation_id: UUID
    assigned_to: UUID
    assigned_by: UUID
    task_type: TaskType
    status: TaskStatus
    priority: Priority
    due_date: Optional[datetime] = None
    instructions: Optional[str] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Filter / Pagination ────────────────────────────────────────────────────────

class OperationFilters(BaseModel):
    status: Optional[OperationStatus] = None
    type: Optional[OperationType] = None
    client_id: Optional[UUID] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    # Free-text match over operation_number and notes. Purely additive: callers
    # that omit it get exactly the previous behaviour.
    search: Optional[str] = None
    page: int = 1
    per_page: int = 20

    @field_validator("per_page")
    @classmethod
    def validate_per_page(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("per_page must be between 1 and 100")
        return v

    @field_validator("page")
    @classmethod
    def validate_page(cls, v: int) -> int:
        if v < 1:
            raise ValueError("page must be >= 1")
        return v
