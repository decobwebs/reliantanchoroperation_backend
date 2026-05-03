from typing import List, Optional
from datetime import datetime
from uuid import UUID, uuid4
import mimetypes

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from fastapi import HTTPException, UploadFile, status

from app.models.document import Document
from app.models.operation import Operation
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, DocType
from app.config import settings


# Allowed MIME types for document upload
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}

SUPABASE_STORAGE_BUCKET = "operation-documents"
MAX_FILE_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(
            and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
        )
    )
    op = result.scalar_one_or_none()
    if not op:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return op


async def _upload_to_supabase(
    file_content: bytes,
    storage_path: str,
    mime_type: str,
    bucket: str = SUPABASE_STORAGE_BUCKET,
) -> str:
    """Upload file to Supabase Storage and return the public URL."""
    upload_url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": mime_type,
        "x-upsert": "false",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(upload_url, content=file_content, headers=headers)

    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Storage upload failed: {resp.text[:200]}",
        )

    public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket}/{storage_path}"
    return public_url


class DocumentService:

    @staticmethod
    async def upload_document(
        operation_id: UUID,
        file: UploadFile,
        document_type: DocType,
        description: Optional[str],
        current_user: User,
        db: AsyncSession,
    ) -> Document:
        await _get_operation_or_404(operation_id, db)

        # Read file content
        content = await file.read()

        # Validate file size
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB",
            )

        # Validate MIME type
        mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"File type '{mime_type}' is not allowed. Allowed: PDF, images, Office docs, CSV, TXT.",
            )

        # Sanitise filename and build storage path
        safe_name = (file.filename or "document").replace("..", "").replace("/", "_").replace("\\", "_")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid4())[:8]
        storage_path = f"{operation_id}/{timestamp}_{unique_id}_{safe_name}"

        # Upload to Supabase Storage
        file_url = await _upload_to_supabase(content, storage_path, mime_type)

        doc = Document(
            operation_id=operation_id,
            uploaded_by=current_user.id,
            document_type=document_type,
            file_name=safe_name,
            file_url=file_url,
            file_size_bytes=len(content),
            mime_type=mime_type,
            description=description.strip() if description else None,
            is_deleted=False,
        )
        db.add(doc)

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="UPLOAD_DOCUMENT",
            entity_type="document",
            entity_id=doc.id,
            changes={"file_name": safe_name, "document_type": document_type.value},
        )
        db.add(audit)

        await db.commit()
        await db.refresh(doc)
        return doc

    @staticmethod
    async def register_document_url(
        operation_id: UUID,
        document_type: DocType,
        file_name: str,
        file_url: str,
        description: Optional[str],
        mime_type: Optional[str],
        current_user: User,
        db: AsyncSession,
    ) -> Document:
        """Register an externally-uploaded document by URL (no file upload)."""
        await _get_operation_or_404(operation_id, db)

        doc = Document(
            operation_id=operation_id,
            uploaded_by=current_user.id,
            document_type=document_type,
            file_name=file_name.strip(),
            file_url=file_url.strip(),
            file_size_bytes=None,
            mime_type=mime_type,
            description=description.strip() if description else None,
            is_deleted=False,
        )
        db.add(doc)

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="REGISTER_DOCUMENT",
            entity_type="document",
            entity_id=doc.id,
            changes={"file_name": file_name, "document_type": document_type.value},
        )
        db.add(audit)

        await db.commit()
        await db.refresh(doc)
        return doc

    @staticmethod
    async def list_documents(
        operation_id: UUID,
        include_deleted: bool,
        db: AsyncSession,
    ) -> List[Document]:
        await _get_operation_or_404(operation_id, db)

        conditions = [Document.operation_id == operation_id]
        if not include_deleted:
            conditions.append(Document.is_deleted == False)

        result = await db.execute(
            select(Document)
            .where(and_(*conditions))
            .order_by(Document.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete_document(
        operation_id: UUID,
        document_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Document:
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.id == document_id,
                    Document.operation_id == operation_id,
                    Document.is_deleted == False,
                )
            )
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        # Only uploader or BM can delete
        if doc.uploaded_by != current_user.id and current_user.role != UserRole.bunker_manager:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the uploader or Bunker Manager can delete this document",
            )

        doc.is_deleted = True

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="DELETE_DOCUMENT",
            entity_type="document",
            entity_id=doc.id,
            changes={"file_name": doc.file_name},
        )
        db.add(audit)

        await db.commit()
        await db.refresh(doc)
        return doc
