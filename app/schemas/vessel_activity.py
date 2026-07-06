from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


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
