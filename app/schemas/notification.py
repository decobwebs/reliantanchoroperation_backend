from typing import Optional, List
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.enums import NotificationType, Priority


class NotificationOut(BaseModel):
    id: UUID
    user_id: UUID
    operation_id: Optional[UUID] = None
    type: NotificationType
    title: str
    message: str
    priority: Priority
    is_read: bool
    read_at: Optional[datetime] = None
    action_url: Optional[str] = None
    delivery_channels: List[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountOut(BaseModel):
    unread_count: int
