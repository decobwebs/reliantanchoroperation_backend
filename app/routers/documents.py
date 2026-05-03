from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import User
from app.models.enums import UserRole, DocType
from app.schemas.common import StandardResponse
from app.schemas.document import DocumentOut
from app.services.document_service import DocumentService

router = APIRouter(tags=["Documents"])


@router.post(
    "/operations/{operation_id}/documents/upload",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    operation_id: UUID,
    file: UploadFile = File(...),
    document_type: DocType = Form(...),
    description: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to Supabase Storage and register it on the operation.
    Accepts multipart/form-data. Max 10MB. Allowed: PDF, images, Office docs, CSV, TXT."""
    doc = await DocumentService.upload_document(
        operation_id, file, document_type, description, current_user, db
    )
    return StandardResponse.ok(
        data=DocumentOut.model_validate(doc).model_dump(),
        message=f"Document '{doc.file_name}' uploaded",
    )


@router.post(
    "/operations/{operation_id}/documents",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_document_url(
    operation_id: UUID,
    document_type: DocType = Form(...),
    file_name: str = Form(...),
    file_url: str = Form(...),
    description: Optional[str] = Form(None),
    mime_type: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register a document by URL (for when the client handles the upload to storage directly)."""
    doc = await DocumentService.register_document_url(
        operation_id, document_type, file_name, file_url, description, mime_type, current_user, db
    )
    return StandardResponse.ok(
        data=DocumentOut.model_validate(doc).model_dump(),
        message=f"Document '{doc.file_name}' registered",
    )


@router.get("/operations/{operation_id}/documents", response_model=StandardResponse)
async def list_documents(
    operation_id: UUID,
    include_deleted: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List documents for an operation. All authenticated staff can view."""
    # Only BM can view deleted docs
    if include_deleted and current_user.role != UserRole.bunker_manager:
        include_deleted = False

    docs = await DocumentService.list_documents(operation_id, include_deleted, db)
    items = [DocumentOut.model_validate(d).model_dump() for d in docs]
    return StandardResponse.ok(data=items, message="Documents retrieved")


@router.delete(
    "/operations/{operation_id}/documents/{document_id}",
    response_model=StandardResponse,
)
async def delete_document(
    operation_id: UUID,
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document. Only the uploader or Bunker Manager can delete."""
    doc = await DocumentService.delete_document(operation_id, document_id, current_user, db)
    return StandardResponse.ok(
        data=DocumentOut.model_validate(doc).model_dump(),
        message="Document deleted",
    )
