from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, field_validator


class SetEtaRequest(BaseModel):
    eta_at: datetime
    reason: Optional[str] = None

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class VesselEtaOut(BaseModel):
    id: UUID
    naval_clearance_vessel_id: UUID
    eta_at: datetime
    reason: Optional[str] = None
    set_by: UUID
    created_at: datetime

    model_config = {"from_attributes": True}
