import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, ForeignKey, ARRAY,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import NotificationType, Priority


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    type = Column(SAEnum(NotificationType, name="notification_type"), nullable=False)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(SAEnum(Priority, name="priority"), default=Priority.normal, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    action_url = Column(Text, nullable=True)
    delivery_channels = Column(ARRAY(String), default=["in_app"], nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="notifications")
    operation = relationship("Operation", back_populates="notifications")
