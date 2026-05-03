import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_id = Column(UUID(as_uuid=True), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    full_name = Column(String(150), nullable=False)
    phone = Column(String(20), nullable=True)
    role = Column(SAEnum(UserRole, name="user_role"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    avatar_url = Column(Text, nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    created_operations = relationship("Operation", foreign_keys="Operation.created_by", back_populates="creator")
    client_operations = relationship("Operation", foreign_keys="Operation.client_id", back_populates="client")
    task_assignments_received = relationship("TaskAssignment", foreign_keys="TaskAssignment.assigned_to", back_populates="assignee")
    task_assignments_given = relationship("TaskAssignment", foreign_keys="TaskAssignment.assigned_by", back_populates="assigner")
    notifications = relationship("Notification", back_populates="user")
    audit_logs = relationship("AuditLog", back_populates="user")
