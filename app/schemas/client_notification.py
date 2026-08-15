from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, field_validator, model_validator


class QueueClientNotificationRequest(BaseModel):
    """Nothing sends here — this only queues recipients for BM approval.
    Two recipient sources, ticked independently: Naval Clearance vessels
    (by id) and Cast Off client emails (by the raw email address, since
    those have no id of their own)."""
    recipient_naval_clearance_vessel_ids: List[UUID] = []
    recipient_cast_off_emails: List[str] = []
    notification_type: str  # stage_update | eta_change | completion | general
    stage: Optional[str] = None
    custom_message: Optional[str] = None

    @field_validator("custom_message", mode="before")
    @classmethod
    def strip_message(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @model_validator(mode="after")
    def at_least_one_recipient(self) -> "QueueClientNotificationRequest":
        if not self.recipient_naval_clearance_vessel_ids and not self.recipient_cast_off_emails:
            raise ValueError("At least one recipient must be selected — nothing is queued without an explicit tick")
        return self


class ApprovePendingNotificationsRequest(BaseModel):
    pending_ids: List[UUID]

    @field_validator("pending_ids")
    @classmethod
    def at_least_one(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("Select at least one queued notification to approve")
        return v


class SendApprovedNotificationsRequest(BaseModel):
    pending_ids: List[UUID]

    @field_validator("pending_ids")
    @classmethod
    def at_least_one(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("Select at least one approved notification to send")
        return v


class RejectPendingNotificationRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v or len(v) < 10:
            raise ValueError("Reason must be at least 10 characters")
        return v


class ClientNotificationRecipientOut(BaseModel):
    source: str  # "naval_clearance" | "cast_off"
    naval_clearance_vessel_id: Optional[UUID] = None
    client_id: Optional[UUID] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    vessel_name: str
    imo_number: Optional[str] = None
    current_eta: Optional[datetime] = None


class PendingClientNotificationOut(BaseModel):
    id: UUID
    operation_id: UUID
    naval_clearance_vessel_id: Optional[UUID] = None
    client_id: Optional[UUID] = None
    source: str
    recipient_email: str
    recipient_name: Optional[str] = None
    notification_type: str
    stage: Optional[str] = None
    subject: str
    status: str
    requested_by: UUID
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    sent_log_id: Optional[UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientNotificationLogOut(BaseModel):
    id: UUID
    operation_id: UUID
    naval_clearance_vessel_id: Optional[UUID] = None
    client_id: Optional[UUID] = None
    recipient_email: str
    recipient_name: str
    notification_type: str
    stage: Optional[str] = None
    subject: str
    sent_by: UUID
    sent_at: datetime
    thread_key: str

    model_config = {"from_attributes": True}
