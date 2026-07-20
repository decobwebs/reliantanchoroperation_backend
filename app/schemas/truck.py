from typing import Optional, Any, List, Dict
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator, model_validator
from app.models.enums import TruckStatus, TruckOpStatus, FeedbackStatus, AuditResult, AuditPhase, TruckWaiverStatus


# ── Truck Registry ─────────────────────────────────────────────────────────────

class TruckCreate(BaseModel):
    truck_number: str
    capacity_mt: Decimal
    chassis_number: Optional[str] = None
    current_location: Optional[str] = None
    photo_url: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("truck_number", mode="before")
    @classmethod
    def strip_truck_number(cls, v: str) -> str:
        return v.strip()

    @field_validator("chassis_number", "current_location", "notes", "photo_url", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckUpdate(BaseModel):
    truck_number: Optional[str] = None
    capacity_mt: Optional[Decimal] = None
    chassis_number: Optional[str] = None
    current_location: Optional[str] = None
    status: Optional[TruckStatus] = None
    photo_url: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    reason: Optional[str] = None  # required by the router for edit-audit-trail

    @field_validator("truck_number", "chassis_number", "current_location", "notes", "photo_url", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckOut(BaseModel):
    id: UUID
    truck_number: str
    capacity_mt: Decimal
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    chassis_number: Optional[str] = None
    truck_licence_url: Optional[str] = None
    calibration_cert_url: Optional[str] = None
    status: TruckStatus
    current_location: Optional[str] = None
    gps_lat: Optional[Decimal] = None
    gps_lng: Optional[Decimal] = None
    photo_url: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Truck Profile ──────────────────────────────────────────────────────────────

class TruckOperationHistoryOut(BaseModel):
    id: UUID
    operation_id: UUID
    operation_number: str
    operation_type: str
    operation_status: str
    product_type: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    quantity_remaining_mt: Optional[Decimal] = None
    variance_mt: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    destination_vessel_name: Optional[str] = None
    departed_parking_at: Optional[datetime] = None
    arrived_loading_at: Optional[datetime] = None
    departed_loading_at: Optional[datetime] = None
    transit_start_at: Optional[datetime] = None
    arrived_discharge_at: Optional[datetime] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    status: TruckOpStatus
    logged_by_id: UUID
    logged_by_name: str
    logged_by_role: str
    notes: Optional[str] = None
    events: List[Any] = []
    created_at: datetime


class TruckStatsOut(BaseModel):
    total_operations: int
    total_loaded_mt: Decimal
    total_discharged_mt: Decimal
    total_variance_mt: Decimal
    total_spillage_mt: Decimal
    efficiency_pct: Optional[float] = None


class TruckProfileOut(BaseModel):
    truck: TruckOut
    stats: TruckStatsOut
    history: List[TruckOperationHistoryOut]


# ── Safety Audit ──────────────────────────────────────────────────────────────

class TruckSafetyAuditCreate(BaseModel):
    phase: AuditPhase = AuditPhase.pre
    conducted_at: Optional[datetime] = None
    result: AuditResult
    checklist: List[Dict[str, Any]] = []  # each item may carry its own checked_at timestamp
    notes: Optional[str] = None
    # Header fields (Safety Officer, Driver, PFI, dates, etc.) — captured once per
    # phase alongside the checklist rather than as pseudo checklist items.
    header: Dict[str, Any] = {}

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckSafetyAuditOut(BaseModel):
    id: UUID
    truck_op_id: UUID
    phase: AuditPhase
    operation_id: UUID
    truck_id: UUID
    conducted_by: UUID
    conductor_name: Optional[str] = None
    conducted_at: datetime
    result: AuditResult
    checklist: List[Any] = []
    header: Dict[str, Any] = {}
    notes: Optional[str] = None
    waivers: List[Any] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WaiveAuditItemRequest(BaseModel):
    phase: AuditPhase = AuditPhase.pre
    item: str
    waiver_notes: Optional[str] = None


# ── TruckOperation CRUD ────────────────────────────────────────────────────────

class TruckOperationCreate(BaseModel):
    truck_id: UUID
    product_type: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    destination_vessel_id: Optional[UUID] = None
    # Sourcing — captured per assignment, not on the truck master (drivers are temporary).
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    vendor_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("loading_location", "discharge_location", "notes", "driver_name", "driver_phone", "vendor_name", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckWaybillLinkRequest(BaseModel):
    """The moment waiver number, plate, and driver are linked together."""
    waiver_id: UUID
    driver_name: str
    driver_phone: str
    vendor_name: Optional[str] = None
    waybill_document_number: Optional[str] = None
    waybill_number: Optional[str] = None

    @field_validator("driver_name", "driver_phone", "vendor_name", "waybill_document_number", "waybill_number", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckWaiverBulkCreate(BaseModel):
    waybill_truck_numbers: List[str]

    @field_validator("waybill_truck_numbers")
    @classmethod
    def clean_numbers(cls, v: List[str]) -> List[str]:
        cleaned = [n.strip().upper() for n in v if n and n.strip()]
        if not cleaned:
            raise ValueError("At least one waiver number is required")
        return cleaned


class TruckWaiverOut(BaseModel):
    id: UUID
    waybill_truck_number: str
    status: TruckWaiverStatus
    added_by: UUID
    created_at: datetime
    # Populated when linked — the truck/operation/driver this waiver was paired
    # with at waybill time. A waiver can only ever be linked once today (no
    # release/reuse mechanism), so this doubles as the full history.
    linked_truck_number: Optional[str] = None
    linked_operation_id: Optional[UUID] = None
    linked_operation_number: Optional[str] = None
    linked_driver_name: Optional[str] = None
    linked_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TruckOperationUpdate(BaseModel):
    product_type: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    quantity_remaining_mt: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    destination_vessel_id: Optional[UUID] = None
    supervisor_id: Optional[UUID] = None
    waybill_number: Optional[str] = None
    departed_parking_at: Optional[datetime] = None
    arrived_loading_at: Optional[datetime] = None
    departed_loading_at: Optional[datetime] = None
    transit_start_at: Optional[datetime] = None
    arrived_discharge_at: Optional[datetime] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("loading_location", "discharge_location", "notes", "waybill_number", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckOperationOut(BaseModel):
    id: UUID
    operation_id: UUID
    truck_id: UUID
    logged_by: UUID
    supervisor_id: Optional[UUID] = None
    product_type: Optional[str] = None
    quantity_loaded_mt: Optional[Decimal] = None
    quantity_discharged_mt: Optional[Decimal] = None
    quantity_remaining_mt: Optional[Decimal] = None
    variance_mt: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    loading_location: Optional[str] = None
    discharge_location: Optional[str] = None
    destination_vessel_id: Optional[UUID] = None
    destination_vessel_name: Optional[str] = None
    discharge_approved: Optional[bool] = None
    discharge_approved_by: Optional[UUID] = None
    discharge_approved_at: Optional[datetime] = None
    departed_parking_at: Optional[datetime] = None
    arrived_loading_at: Optional[datetime] = None
    departed_loading_at: Optional[datetime] = None
    transit_start_at: Optional[datetime] = None
    arrived_discharge_at: Optional[datetime] = None
    transit_end_at: Optional[datetime] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    gps_start_lat: Optional[Decimal] = None
    gps_start_lng: Optional[Decimal] = None
    gps_end_lat: Optional[Decimal] = None
    gps_end_lng: Optional[Decimal] = None
    waybill_number: Optional[str] = None
    waybill_url: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    vendor_name: Optional[str] = None
    waybill_document_number: Optional[str] = None
    waiver_id: Optional[UUID] = None
    status: TruckOpStatus
    notes: Optional[str] = None
    events: List[Any] = []
    created_at: datetime
    updated_at: datetime
    truck: Optional[TruckOut] = None
    supervisor: Optional[Any] = None
    safety_audits: List[TruckSafetyAuditOut] = []

    model_config = {"from_attributes": True}


# ── Timeline milestone requests ────────────────────────────────────────────────

class TruckEventRequest(BaseModel):
    event_type: str
    description: str
    timestamp: Optional[datetime] = None

    @field_validator("event_type", "description", mode="before")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class TruckTransitRequest(BaseModel):
    gps_lat: Optional[Decimal] = None
    gps_lng: Optional[Decimal] = None


class TruckDepartParkingRequest(BaseModel):
    departed_parking_at: Optional[datetime] = None
    gps_lat: Optional[Decimal] = None
    gps_lng: Optional[Decimal] = None
    notes: Optional[str] = None


class TruckArrivedLoadingRequest(BaseModel):
    arrived_loading_at: Optional[datetime] = None
    loading_location: Optional[str] = None
    notes: Optional[str] = None


class TruckDepartedLoadingRequest(BaseModel):
    departed_loading_at: Optional[datetime] = None
    quantity_loaded_mt: Decimal
    product_type: Optional[str] = None
    notes: Optional[str] = None


class TruckArrivedDischargeRequest(BaseModel):
    arrived_discharge_at: Optional[datetime] = None
    discharge_location: Optional[str] = None
    notes: Optional[str] = None


class TruckDischargeEndRequest(BaseModel):
    quantity_discharged_mt: Decimal
    quantity_remaining_mt: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    discharge_end_at: Optional[datetime] = None
    destination_vessel_id: Optional[UUID] = None
    destination_vessel_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("destination_vessel_name", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class DischargeApproveRequest(BaseModel):
    notes: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class DischargeEditRequest(BaseModel):
    quantity_discharged_mt: Optional[Decimal] = None
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    destination_vessel_id: Optional[UUID] = None
    destination_vessel_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("destination_vessel_name", "notes", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TruckCompletionRequest(BaseModel):
    """Supervisor submits final completion report, triggers pending_completion."""
    readiness_summary: str
    notes: Optional[str] = None

    @field_validator("readiness_summary", mode="before")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


# ── Truck Feedback ─────────────────────────────────────────────────────────────

class TruckFeedbackCreate(BaseModel):
    truck_ids: List[UUID]
    readiness_summary: str
    truck_details: Any

    @field_validator("readiness_summary", mode="before")
    @classmethod
    def strip_summary(cls, v: str) -> str:
        return v.strip()


class TruckFeedbackOut(BaseModel):
    id: UUID
    operation_id: UUID
    submitted_by: UUID
    reviewed_by: Optional[UUID] = None
    truck_ids: Any
    status: FeedbackStatus
    readiness_summary: str
    truck_details: Any
    rejection_reason: Optional[str] = None
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    version: int

    model_config = {"from_attributes": True}


class FeedbackApproveRequest(BaseModel):
    notes: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class FeedbackRejectRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason_length(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("Rejection reason must be at least 10 characters")
        return v


# ── Vessel Discharge Events ────────────────────────────────────────────────────

class VesselDischargeEventCreate(BaseModel):
    source_vessel_id: UUID
    destination_vessel_id: Optional[UUID] = None
    product_type: Optional[str] = None
    quantity_mt: Decimal
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    density: Optional[Decimal] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class VesselDischargeEventOut(BaseModel):
    id: UUID
    operation_id: UUID
    source_vessel_id: UUID
    destination_vessel_id: Optional[UUID] = None
    product_type: Optional[str] = None
    quantity_mt: Decimal
    spillage_mt: Optional[Decimal] = None
    temperature_celsius: Optional[Decimal] = None
    density: Optional[Decimal] = None
    discharge_start_at: Optional[datetime] = None
    discharge_end_at: Optional[datetime] = None
    supervisor_id: Optional[UUID] = None
    rob_entry_id: Optional[UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
