from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, field_validator


class SendClientNotificationRequest(BaseModel):
    recipient_naval_clearance_vessel_ids: List[UUID]
    notification_type: str  # stage_update | eta_change | completion | general
    stage: Optional[str] = None
    custom_message: Optional[str] = None

    @field_validator("custom_message", mode="before")
    @classmethod
    def strip_message(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("recipient_naval_clearance_vessel_ids")
    @classmethod
    def at_least_one_recipient(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("At least one recipient must be selected — nothing sends without an explicit tick")
        return v


class ClientNotificationRecipientOut(BaseModel):
    naval_clearance_vessel_id: UUID
    client_id: UUID
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    vessel_name: str
    imo_number: Optional[str] = None
    current_eta: Optional[datetime] = None


class ClientNotificationLogOut(BaseModel):
    id: UUID
    operation_id: UUID
    naval_clearance_vessel_id: UUID
    client_id: UUID
    recipient_email: str
    recipient_name: str
    notification_type: str
    stage: Optional[str] = None
    subject: str
    sent_by: UUID
    sent_at: datetime
    thread_key: str

    model_config = {"from_attributes": True}
