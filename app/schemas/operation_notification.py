from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, field_validator


class SendOperationNotificationRequest(BaseModel):
    """BM picks either all active staff (recipient_user_ids omitted/empty
    with all_staff=True) or a specific subset."""
    all_staff: bool = False
    recipient_user_ids: List[UUID] = []
    title: str
    message: str

    @field_validator("title", "message", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("title")
    @classmethod
    def title_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A title is required")
        return v

    @field_validator("message")
    @classmethod
    def message_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A message is required")
        return v


class StaffRecipientOut(BaseModel):
    id: UUID
    full_name: str
    role: str


class OperationNotificationRecipientOut(BaseModel):
    id: UUID
    user_id: UUID
    user_name: Optional[str] = None

    model_config = {"from_attributes": True}


class OperationNotificationOut(BaseModel):
    id: UUID
    operation_id: UUID
    sent_by: UUID
    sent_by_name: Optional[str] = None
    title: str
    message: str
    sent_at: datetime
    recipients: List[OperationNotificationRecipientOut] = []

    model_config = {"from_attributes": True}
