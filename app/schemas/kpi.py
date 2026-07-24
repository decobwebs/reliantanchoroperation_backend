from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class VesselRunKpi(BaseModel):
    vessel_activity_id: UUID
    vessel_name: Optional[str] = None
    cast_off_at: Optional[datetime] = None
    discharge_completed_at: Optional[datetime] = None
    duration_hours: Optional[float] = None


class OperationKpiOut(BaseModel):
    operation_id: UUID
    cast_off_at: Optional[datetime] = None          # earliest across all vessel runs
    discharge_completed_at: Optional[datetime] = None  # latest across all vessel runs
    duration_hours: Optional[float] = None
    vessel_runs: List[VesselRunKpi]


class StageDurationEntry(BaseModel):
    vessel_activity_id: UUID
    stage: str
    role: Optional[str] = None
    user_name: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_hours: Optional[float] = None


class RoleStageDurationsOut(BaseModel):
    operation_id: UUID
    entries: List[StageDurationEntry]
