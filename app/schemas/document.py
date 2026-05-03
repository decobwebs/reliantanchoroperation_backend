from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.enums import DocType


class DocumentOut(BaseModel):
    id: UUID
    operation_id: UUID
    uploaded_by: UUID
    document_type: DocType
    file_name: str
    file_url: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    description: Optional[str] = None
    is_deleted: bool
    created_at: datetime

    model_config = {"from_attributes": True}
