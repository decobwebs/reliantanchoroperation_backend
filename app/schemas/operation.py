from typing import Optional, List, Any, Dict
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator
from app.models.enums import (
    OperationType, OperationStatus, TaskType, TaskStatus, Priority, ProductType
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
    client_id: UUID
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


class UpdateOperationRequest(BaseModel):
    actual_volume_mt: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    vessel_id: Optional[UUID] = None
    reason: Optional[str] = None  # why this edit was made — surfaced in the Activity tab


class TransitionRequest(BaseModel):
    to_status: OperationStatus
    reason: Optional[str] = None
    completion_notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


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


class OperationOut(BaseModel):
    id: UUID
    operation_number: str
    type: OperationType
    status: OperationStatus
    products: List[OperationProductOut] = []
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    client_id: UUID
    created_by: UUID
    actual_volume_mt: Optional[Decimal] = None
    notes: Optional[str] = None
    paused_at: Optional[datetime] = None
    paused_reason: Optional[str] = None
    completed_at: Optional[datetime] = None
    completion_notes: Optional[str] = None
    currency: str
    vessel_id: Optional[UUID] = None
    trucks_required: Optional[int] = None
    version: int = 1
    parent_operation_id: Optional[UUID] = None
    version_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


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
