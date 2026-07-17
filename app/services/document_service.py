from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
import mimetypes
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, UploadFile, status

from app.models.document import Document
from app.models.operation import Operation
from app.models.audit import AuditLog
from app.models.user import User
from app.models.finance import PFI, Invoice
from app.models.bdn import BDN
from app.models.enums import UserRole, DocType
from app.config import settings


@dataclass
class UnifiedDocItem:
    id: str
    source_type: str          # "upload" | "pfi" | "pfi_receipt" | "invoice" | "bdn"
    source_id: str
    operation_id: Optional[str]
    operation_number: Optional[str]
    document_type: str        # normalised category for filtering / display
    file_name: str
    file_url: str
    file_size_bytes: Optional[int]
    description: Optional[str]
    created_at: datetime
    uploader_name: Optional[str]
    uploader_role: Optional[str]
    source_ref: Optional[str] # PFI-2026-001, INV-2026-001, BDN-2026-001 …
    mime_type: Optional[str] = None


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


async def ensure_storage_bucket(bucket: str = SUPABASE_STORAGE_BUCKET) -> None:
    """Create the Supabase storage bucket if it does not already exist."""
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    bucket_url = f"{settings.SUPABASE_URL}/storage/v1/bucket"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{bucket_url}/{bucket}", headers=headers)
            if resp.status_code == 200:
                _logger.info("Supabase bucket '%s' already exists.", bucket)
                return

            _logger.info(
                "Bucket '%s' not found (status %s) — attempting to create it.",
                bucket, resp.status_code,
            )
            create_resp = await client.post(
                bucket_url,
                headers=headers,
                json={"id": bucket, "name": bucket, "public": False},
            )
            if create_resp.status_code in (200, 201, 409):
                _logger.info("Supabase bucket '%s' ready.", bucket)
            else:
                _logger.warning(
                    "Could not create Supabase bucket '%s': HTTP %s — %s",
                    bucket, create_resp.status_code, create_resp.text[:300],
                )
    except httpx.RequestError as exc:
        _logger.warning(
            "Supabase Storage unreachable during bucket init (%s) — uploads may fail.",
            exc,
        )


import logging as _logging
_logger = _logging.getLogger(__name__)


async def _do_upload(
    file_content: bytes,
    storage_path: str,
    mime_type: str,
    bucket: str,
) -> Optional[httpx.Response]:
    upload_url = f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": mime_type,
        "x-upsert": "false",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(upload_url, content=file_content, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Supabase Storage is unreachable. Check SUPABASE_URL, "
                "DNS/internet connectivity, and firewall/VPN settings."
            ),
        ) from exc


async def _upload_to_supabase(
    file_content: bytes,
    storage_path: str,
    mime_type: str,
    bucket: str = SUPABASE_STORAGE_BUCKET,
) -> str:
    """Upload file to Supabase Storage and return the public URL."""
    resp = await _do_upload(file_content, storage_path, mime_type, bucket)

    if resp.status_code not in (200, 201):
        # If bucket is missing, create it and retry once
        body_text = resp.text
        if resp.status_code == 404 or "Bucket not found" in body_text or "bucket" in body_text.lower():
            _logger.info("Bucket '%s' not found — auto-creating and retrying upload", bucket)
            await ensure_storage_bucket(bucket)
            resp = await _do_upload(file_content, storage_path, mime_type, bucket)
            if resp.status_code not in (200, 201):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Storage upload failed after bucket creation: {resp.text[:200]}",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Storage upload failed: {resp.text[:200]}",
            )

    # Store the non-public path so _storage_path_from_supabase_url can extract it
    # at read time and generate a signed URL. Existing rows using /public/ are
    # also handled by _storage_path_from_supabase_url (checks both prefixes).
    return f"{settings.SUPABASE_URL}/storage/v1/object/{bucket}/{storage_path}"


def _storage_path_from_supabase_url(
    file_url: Optional[str],
    bucket: str = SUPABASE_STORAGE_BUCKET,
) -> Optional[str]:
    if not file_url:
        return None

    base_url = settings.SUPABASE_URL.rstrip("/")
    prefixes = (
        f"{base_url}/storage/v1/object/public/{bucket}/",
        f"{base_url}/storage/v1/object/{bucket}/",
    )
    for prefix in prefixes:
        if file_url.startswith(prefix):
            return file_url[len(prefix):]
    return None


async def create_signed_supabase_url(
    file_url: Optional[str],
    expires_in: int = 3600,
    bucket: str = SUPABASE_STORAGE_BUCKET,
) -> Optional[str]:
    """Return a temporary download URL for files stored in a private Supabase bucket."""
    storage_path = _storage_path_from_supabase_url(file_url, bucket=bucket)
    if not storage_path:
        return file_url

    encoded_path = quote(storage_path, safe="/")
    sign_url = f"{settings.SUPABASE_URL}/storage/v1/object/sign/{bucket}/{encoded_path}"
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(sign_url, json={"expiresIn": expires_in}, headers=headers)
    except httpx.RequestError:
        return file_url

    if resp.status_code not in (200, 201):
        return file_url

    signed_url = resp.json().get("signedURL")
    if not signed_url:
        return file_url
    if signed_url.startswith("http"):
        return signed_url
    if signed_url.startswith("/object/"):
        return f"{settings.SUPABASE_URL}/storage/v1{signed_url}"
    return f"{settings.SUPABASE_URL}{signed_url}"


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
    async def list_all_documents(
        db: AsyncSession,
        keyword: Optional[str] = None,
        operation_id: Optional[UUID] = None,
        client_id: Optional[UUID] = None,
        uploaded_by_id: Optional[UUID] = None,
        document_type: Optional[DocType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Tuple[List[Document], int]:
        conditions = [Document.is_deleted == False]

        if keyword:
            kw = f"%{keyword.lower()}%"
            conditions.append(
                or_(
                    func.lower(Document.file_name).like(kw),
                    func.lower(Document.description).like(kw),
                )
            )
        if operation_id:
            conditions.append(Document.operation_id == operation_id)
        if document_type:
            conditions.append(Document.document_type == document_type)
        if uploaded_by_id:
            conditions.append(Document.uploaded_by == uploaded_by_id)
        if date_from:
            conditions.append(Document.created_at >= date_from)
        if date_to:
            conditions.append(Document.created_at <= date_to)

        # Apply client_id filter via join to Operation
        base_stmt = (
            select(Document)
            .join(Operation, Document.operation_id == Operation.id)
            .options(
                selectinload(Document.operation),
                selectinload(Document.uploader),
            )
            .where(and_(*conditions))
        )
        if client_id:
            base_stmt = base_stmt.where(Operation.client_id == client_id)

        count_stmt = select(func.count()).select_from(
            select(Document)
            .join(Operation, Document.operation_id == Operation.id)
            .where(and_(*conditions))
            .subquery()
        )
        if client_id:
            count_stmt = select(func.count()).select_from(
                select(Document)
                .join(Operation, Document.operation_id == Operation.id)
                .where(and_(*conditions, Operation.client_id == client_id))
                .subquery()
            )

        total_result = await db.execute(count_stmt)
        total = total_result.scalar_one()

        offset = (page - 1) * per_page
        result = await db.execute(
            base_stmt.order_by(Document.created_at.desc()).offset(offset).limit(per_page)
        )
        docs = list(result.scalars().all())

        # Attach computed fields for hub display
        for doc in docs:
            op = getattr(doc, "operation", None)
            uploader = getattr(doc, "uploader", None)
            doc.operation_number = op.operation_number if op else None
            doc.operation_id_str = str(op.id) if op else None
            doc.client_id_str = str(op.client_id) if op else None
            doc.uploader_name = uploader.full_name if uploader else None
            doc.uploader_role = uploader.role.value if uploader else None

        return docs, total

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

    @staticmethod
    async def list_unified_documents(
        db: AsyncSession,
        keyword: Optional[str] = None,
        document_type: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        page: int = 1,
        per_page: int = 25,
    ) -> Tuple[List[UnifiedDocItem], int]:
        items: List[UnifiedDocItem] = []

        # ── 1. Uploaded documents (Document table) ────────────────────────────
        doc_result = await db.execute(
            select(Document, Operation, User)
            .join(Operation, Document.operation_id == Operation.id)
            .join(User, Document.uploaded_by == User.id)
            .where(Document.is_deleted == False)
            .limit(1000)
        )
        for doc, op, user in doc_result.all():
            items.append(UnifiedDocItem(
                id=str(doc.id),
                source_type="upload",
                source_id=str(doc.id),
                operation_id=str(op.id) if op else None,
                operation_number=op.operation_number if op else None,
                document_type=doc.document_type.value if doc.document_type else "other",
                file_name=doc.file_name,
                file_url=doc.file_url,
                file_size_bytes=doc.file_size_bytes,
                description=doc.description,
                created_at=doc.created_at,
                uploader_name=user.full_name if user else None,
                uploader_role=user.role.value if user else None,
                source_ref=None,
                mime_type=doc.mime_type,
            ))

        # ── 2. PFI documents (document_url) ───────────────────────────────────
        pfi_doc_result = await db.execute(
            select(PFI, Operation, User)
            .outerjoin(Operation, PFI.operation_id == Operation.id)
            .join(User, PFI.linked_by == User.id)
            .where(PFI.document_url.isnot(None))
            .limit(500)
        )
        for pfi, op, user in pfi_doc_result.all():
            items.append(UnifiedDocItem(
                id=f"pfi-{pfi.id}",
                source_type="pfi",
                source_id=str(pfi.id),
                operation_id=str(op.id) if op else None,
                operation_number=op.operation_number if op else None,
                document_type="pfi",
                file_name=f"{pfi.pfi_number}.pdf",
                file_url=pfi.document_url,
                file_size_bytes=None,
                description=pfi.description or f"PFI document — {pfi.pfi_number}",
                created_at=pfi.created_at,
                uploader_name=user.full_name if user else None,
                uploader_role=user.role.value if user else None,
                source_ref=pfi.pfi_number,
            ))

        # ── 3. PFI payment receipts (receipt_url) ────────────────────────────
        pfi_rcpt_result = await db.execute(
            select(PFI, Operation, User)
            .outerjoin(Operation, PFI.operation_id == Operation.id)
            .join(User, PFI.linked_by == User.id)
            .where(PFI.receipt_url.isnot(None))
            .limit(500)
        )
        for pfi, op, user in pfi_rcpt_result.all():
            items.append(UnifiedDocItem(
                id=f"pfi-receipt-{pfi.id}",
                source_type="pfi_receipt",
                source_id=str(pfi.id),
                operation_id=str(op.id) if op else None,
                operation_number=op.operation_number if op else None,
                document_type="payment_receipt",
                file_name=f"{pfi.pfi_number}-receipt.pdf",
                file_url=pfi.receipt_url,
                file_size_bytes=None,
                description=f"Payment receipt for {pfi.pfi_number}",
                created_at=pfi.confirmed_at or pfi.created_at,
                uploader_name=user.full_name if user else None,
                uploader_role=user.role.value if user else None,
                source_ref=pfi.pfi_number,
            ))

        # ── 4. Invoice PDFs ───────────────────────────────────────────────────
        inv_result = await db.execute(
            select(Invoice, Operation, User)
            # OUTER join: standalone invoices have no operation and must still
            # appear in the document hub (an inner join silently hid them).
            .outerjoin(Operation, Invoice.operation_id == Operation.id)
            .join(User, Invoice.generated_by == User.id)
            .where(Invoice.pdf_url.isnot(None))
            .limit(500)
        )
        for inv, op, user in inv_result.all():
            items.append(UnifiedDocItem(
                id=f"invoice-{inv.id}",
                source_type="invoice",
                source_id=str(inv.id),
                operation_id=str(op.id) if op else None,
                operation_number=op.operation_number if op else None,
                document_type="invoice",
                file_name=f"{inv.invoice_number}.pdf",
                file_url=inv.pdf_url,
                file_size_bytes=None,
                description=f"Invoice {inv.invoice_number} — {inv.currency} {inv.total_amount}",
                created_at=inv.created_at,
                uploader_name=user.full_name if user else None,
                uploader_role=user.role.value if user else None,
                source_ref=inv.invoice_number,
            ))

        # ── 5. BDN PDFs ───────────────────────────────────────────────────────
        bdn_result = await db.execute(
            select(BDN, Operation, User)
            .join(Operation, BDN.operation_id == Operation.id)
            .join(User, BDN.generated_by == User.id)
            .where(BDN.pdf_url.isnot(None))
            .limit(500)
        )
        for bdn, op, user in bdn_result.all():
            items.append(UnifiedDocItem(
                id=f"bdn-{bdn.id}",
                source_type="bdn",
                source_id=str(bdn.id),
                operation_id=str(op.id) if op else None,
                operation_number=op.operation_number if op else None,
                document_type="bdn",
                file_name=f"{bdn.bdn_number}.pdf",
                file_url=bdn.pdf_url,
                file_size_bytes=None,
                description=f"BDN {bdn.bdn_number} — {bdn.quantity_delivered_mt} MT",
                created_at=bdn.created_at,
                uploader_name=user.full_name if user else None,
                uploader_role=user.role.value if user else None,
                source_ref=bdn.bdn_number,
            ))

        # ── Apply filters ─────────────────────────────────────────────────────
        if keyword:
            kw = keyword.lower()
            items = [
                i for i in items
                if kw in i.file_name.lower()
                or (i.description and kw in i.description.lower())
                or (i.operation_number and kw in i.operation_number.lower())
                or (i.source_ref and kw in i.source_ref.lower())
            ]
        if document_type:
            items = [i for i in items if i.document_type == document_type]
        if date_from:
            items = [i for i in items if i.created_at.replace(tzinfo=None) >= date_from.replace(tzinfo=None)]
        if date_to:
            items = [i for i in items if i.created_at.replace(tzinfo=None) <= date_to.replace(tzinfo=None)]

        items.sort(key=lambda x: x.created_at, reverse=True)

        total = len(items)
        offset = (page - 1) * per_page
        return items[offset : offset + per_page], total
