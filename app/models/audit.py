import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, ForeignKey, ARRAY,
    Integer, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import UserRole


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    changes = Column(JSONB, nullable=True)
    reason = Column(Text, nullable=True)
    # Set when `user_id` (the real actor) performed this action while
    # acting-as another role — user_id always stays the real actor's id.
    acted_as_role = Column(SAEnum(UserRole, name="user_role"), nullable=True)
    ip_address = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="audit_logs")
    operation = relationship("Operation", back_populates="audit_logs")


class DelegationAssignment(Base):
    __tablename__ = "delegation_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delegator_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    delegate_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    permission_scope = Column(ARRAY(String), nullable=False)
    reason = Column(Text, nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_auto_escalation = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    delegator = relationship("User", foreign_keys=[delegator_id])
    delegate = relationship("User", foreign_keys=[delegate_id])


class ClientMilestone(Base):
    __tablename__ = "client_milestones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    milestone_type = Column(String(100), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    reached_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    is_visible = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="milestones")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSONB, nullable=False)
    description = Column(Text, nullable=True)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    updater = relationship("User", foreign_keys=[updated_by])
