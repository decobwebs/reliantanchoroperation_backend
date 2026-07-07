import asyncio
from typing import Optional
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.dependencies import get_current_user, require_roles, get_request_meta
from app.models.audit import AuditLog
from app.models.document import Document
from app.models.user import User
from app.models.enums import UserRole, DocType
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.document import DocumentOut, DocumentHubOut
from app.services.document_service import DocumentService, UnifiedDocItem, create_signed_supabase_url
from app.services.operation_service import OperationService
from app.config import settings

router = APIRouter(tags=["Documents"])


async def _assert_can_access_operation(operation_id: UUID, current_user: User, db: AsyncSession) -> None:
    """Object-level authz: raises 403/404 unless the caller may see this operation.

    Reuses the operation visibility rules (BM/FM = all, client = own, task-assigned
    staff = their operations) so document access can never leak across clients.
    """
    await OperationService.get_operation(operation_id, current_user, db)


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
    await _assert_can_access_operation(operation_id, current_user, db)
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
    await _assert_can_access_operation(operation_id, current_user, db)
    # Only accept Supabase storage URLs / bare storage paths — never arbitrary
    # external links (which would be stored verbatim and later clickable in the hub).
    _u = (file_url or "").strip()
    _allowed = (not _u.startswith(("http://", "https://"))) or _u.startswith(settings.SUPABASE_URL)
    if not _u or not _allowed:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file_url must be a Supabase storage URL or path.",
        )
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
    """List documents for an operation. Restricted to users who can see the operation."""
    await _assert_can_access_operation(operation_id, current_user, db)
    # Only BM can view deleted docs
    if include_deleted and current_user.role != UserRole.bunker_manager:
        include_deleted = False

    docs = await DocumentService.list_documents(operation_id, include_deleted, db)
    items = []
    for d in docs:
        item = DocumentOut.model_validate(d).model_dump()
        item["file_url"] = await create_signed_supabase_url(item.get("file_url"))
        items.append(item)
    return StandardResponse.ok(data=items, message="Documents retrieved")


@router.get("/documents", response_model=PaginatedResponse)
async def list_all_documents(
    keyword: Optional[str] = Query(None, description="Search file name or description"),
    operation_id: Optional[UUID] = Query(None),
    client_id: Optional[UUID] = Query(None),
    uploaded_by_id: Optional[UUID] = Query(None),
    document_type: Optional[DocType] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Global document hub — all operations. Bunker Manager only."""
    docs, total = await DocumentService.list_all_documents(
        db,
        keyword=keyword,
        operation_id=operation_id,
        client_id=client_id,
        uploaded_by_id=uploaded_by_id,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )

    async def _serialize(doc) -> dict:
        item = DocumentHubOut.model_validate(doc).model_dump()
        item["file_url"] = await create_signed_supabase_url(item.get("file_url"))
        item["operation_number"] = getattr(doc, "operation_number", None)
        item["operation_id_str"] = getattr(doc, "operation_id_str", None)
        item["uploader_name"] = getattr(doc, "uploader_name", None)
        item["uploader_role"] = getattr(doc, "uploader_role", None)
        item["client_id"] = getattr(doc, "client_id_str", None)
        return item

    items = await asyncio.gather(*[_serialize(d) for d in docs])
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


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


@router.get("/documents/hub", response_model=PaginatedResponse)
async def unified_document_hub(
    keyword: Optional[str] = Query(None),
    document_type: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Unified document hub — all uploads, PFI docs, invoices and BDNs. Bunker Manager only."""
    items, total = await DocumentService.list_unified_documents(
        db,
        keyword=keyword,
        document_type=document_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )

    async def _to_dict(item: UnifiedDocItem) -> dict:
        return {
            "id":               item.id,
            "source_type":      item.source_type,
            "source_id":        item.source_id,
            "operation_id":     item.operation_id,
            "operation_number": item.operation_number,
            "document_type":    item.document_type,
            "file_name":        item.file_name,
            "file_url":         await create_signed_supabase_url(item.file_url),
            "file_size_bytes":  item.file_size_bytes,
            "description":      item.description,
            "created_at":       item.created_at.isoformat() if item.created_at else None,
            "uploader_name":    item.uploader_name,
            "uploader_role":    item.uploader_role,
            "source_ref":       item.source_ref,
            "mime_type":        item.mime_type,
        }

    serialized = await asyncio.gather(*[_to_dict(i) for i in items])
    return PaginatedResponse.ok(items=list(serialized), total=total, page=page, per_page=per_page)


@router.get("/documents/{document_id}/download", response_model=StandardResponse)
async def get_document_download_url(
    document_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the download URL for a document and record the access in the audit log."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.is_deleted == False,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Object-level authz: caller must be allowed to see this document's operation.
    await _assert_can_access_operation(doc.operation_id, current_user, db)

    meta = get_request_meta(request)
    audit = AuditLog(
        user_id=current_user.id,
        operation_id=doc.operation_id,
        action="DOWNLOAD_DOCUMENT",
        entity_type="document",
        entity_id=doc.id,
        changes={
            "file_name": doc.file_name,
            "document_type": doc.document_type.value if doc.document_type else None,
        },
        ip_address=meta["ip"],
        user_agent=meta["user_agent"],
    )
    db.add(audit)
    await db.flush()

    signed_url = await create_signed_supabase_url(doc.file_url)
    return StandardResponse.ok(
        data={"url": signed_url, "file_name": doc.file_name},
        message="Download URL retrieved",
    )
