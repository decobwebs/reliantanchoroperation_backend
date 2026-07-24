from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.enums import DocType


class DocumentOut(BaseModel):
    id: UUID
    operation_id: UUID
    vessel_activity_id: Optional[UUID] = None
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


class DocumentHubOut(DocumentOut):
    """Extended output for the global document hub — includes operation and uploader context."""
    operation_number: Optional[str] = None
    operation_id_str: Optional[str] = None
    uploader_name: Optional[str] = None
    uploader_role: Optional[str] = None
    client_id: Optional[str] = None
