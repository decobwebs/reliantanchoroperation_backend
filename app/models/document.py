import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, BigInteger, ForeignKey,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import DocType


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id"), nullable=True)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    document_type = Column(SAEnum(DocType, name="doc_type"), nullable=False)
    file_name = Column(String(500), nullable=False)
    file_url = Column(Text, nullable=False)
    file_size_bytes = Column(BigInteger, nullable=True)
    mime_type = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    operation = relationship("Operation", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by])
