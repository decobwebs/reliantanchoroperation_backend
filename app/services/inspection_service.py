"""Inspections and compliance checks, with follow-up actions.

Same house rules as the vessel logs (vessel_record_service): nothing is
deleted or rewritten without trace, every change after the first write
carries a written reason and lands on the audit log, attachments are only
ever added.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import UserRole
from app.models.inspection import Inspection, InspectionAction
from app.models.operation import Operation
from app.models.truck import Truck
from app.models.user import User
from app.models.vessel import Vessel
from app.permissions import INSPECTION_VIEWER_ROLES
from app.schemas.inspection import (
    InspectionCreate, InspectionUpdate, InspectionActionCreate, InspectionActionUpdate,
    InspectionActionComplete, InspectionActionReopen,
)
from app.services.audit_diff import capture_diff
from app.services.notification_service import notify
from app.services.vessel_record_service import (
    _attachment_link, _attachments_out, _audit, _refuse_blanked, _store_attachment,
)

_INSPECTION_REQUIRED = ("inspected_on", "title", "result")
_ACTION_REQUIRED = ("description", "owner_id", "due_date")


def _inspection_out(i: Inspection, today: Optional[date] = None) -> Dict[str, Any]:
    """Requires .vessel/.truck/.operation/.inspector/.actions(+owner/finisher) eager-loaded."""
    today = today or date.today()
    return {
        "id": str(i.id),
        "subject_type": i.subject_type,
        "vessel_id": str(i.vessel_id) if i.vessel_id else None,
        "vessel_name": i.vessel.vessel_name if i.vessel else None,
        "truck_id": str(i.truck_id) if i.truck_id else None,
        "truck_number": i.truck.truck_number if i.truck else None,
        "operation_id": str(i.operation_id) if i.operation_id else None,
        "operation_number": i.operation.operation_number if i.operation else None,
        "site_name": i.site_name,
        "inspected_on": i.inspected_on.isoformat(),
        "title": i.title,
        "result": i.result,
        "findings": i.findings,
        "inspector_id": str(i.inspector_id),
        "inspector_name": i.inspector.full_name if i.inspector else None,
        "attachments": _attachments_out(i.attachments),
        "actions": [_action_out(a, today) for a in i.actions],
        "created_at": i.created_at.isoformat(),
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


def _action_out(a: InspectionAction, today: date) -> Dict[str, Any]:
    return {
        "id": str(a.id),
        "inspection_id": str(a.inspection_id),
        "description": a.description,
        "owner_id": str(a.owner_id),
        "owner_name": a.owner.full_name if a.owner else None,
        "due_date": a.due_date.isoformat(),
        "overdue": a.status == "open" and a.due_date < today,
        "status": a.status,
        "done_note": a.done_note,
        "done_by_name": a.finisher.full_name if a.finisher else None,
        "done_at": a.done_at.isoformat() if a.done_at else None,
        "created_at": a.created_at.isoformat(),
    }


def _query():
    return select(Inspection).options(
        selectinload(Inspection.vessel),
        selectinload(Inspection.truck),
        selectinload(Inspection.operation),
        selectinload(Inspection.inspector),
        selectinload(Inspection.actions).selectinload(InspectionAction.owner),
        selectinload(Inspection.actions).selectinload(InspectionAction.finisher),
    )


async def _owner_or_400(owner_id: UUID, db: AsyncSession) -> User:
    owner = await db.get(User, owner_id)
    if not owner or not owner.is_active or owner.role == UserRole.client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The owner of an action must be an active staff member",
        )
    return owner


class InspectionService:

    @staticmethod
    async def _get(inspection_id: UUID, db: AsyncSession) -> Inspection:
        result = await db.execute(_query().where(Inspection.id == inspection_id))
        inspection = result.scalar_one_or_none()
        if not inspection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inspection not found")
        return inspection

    @staticmethod
    async def _get_action(action_id: UUID, db: AsyncSession) -> InspectionAction:
        result = await db.execute(
            select(InspectionAction)
            .options(selectinload(InspectionAction.inspection))
            .where(InspectionAction.id == action_id)
        )
        action = result.scalar_one_or_none()
        if not action:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")
        return action

    @staticmethod
    async def _reload(inspection_id: UUID, db: AsyncSession) -> Dict[str, Any]:
        await db.flush()
        # Fresh load so the actions list and every name on it are current.
        result = await db.execute(
            _query().where(Inspection.id == inspection_id).execution_options(populate_existing=True)
        )
        return _inspection_out(result.scalar_one())

    @staticmethod
    async def list_inspections(
        db: AsyncSession,
        subject_type: Optional[str] = None,
        vessel_id: Optional[UUID] = None,
        truck_id: Optional[UUID] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        conditions = []
        if subject_type:
            conditions.append(Inspection.subject_type == subject_type)
        if vessel_id:
            conditions.append(Inspection.vessel_id == vessel_id)
        if truck_id:
            conditions.append(Inspection.truck_id == truck_id)
        result = await db.execute(
            _query().where(and_(True, *conditions))
            .order_by(Inspection.inspected_on.desc(), Inspection.created_at.desc())
            .limit(limit)
        )
        today = date.today()
        return [_inspection_out(i, today) for i in result.scalars().all()]

    @staticmethod
    async def get_inspection(inspection_id: UUID, db: AsyncSession) -> Dict[str, Any]:
        return _inspection_out(await InspectionService._get(inspection_id, db))

    @staticmethod
    async def create_inspection(
        data: InspectionCreate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        if data.inspected_on > date.today():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An inspection cannot be dated in the future")

        subject = ""
        operation = None
        if data.subject_type == "vessel":
            vessel = await db.get(Vessel, data.vessel_id)
            if not vessel:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found")
            subject = vessel.vessel_name
        elif data.subject_type == "truck":
            truck = await db.get(Truck, data.truck_id)
            if not truck:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")
            subject = truck.truck_number
        elif data.subject_type == "operation":
            result = await db.execute(
                select(Operation).where(and_(
                    Operation.operation_number == data.operation_number.upper(),
                    Operation.deleted_at.is_(None),
                ))
            )
            operation = result.scalar_one_or_none()
            if not operation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No operation numbered {data.operation_number}",
                )
            subject = operation.operation_number
        else:
            subject = data.site_name

        owners = {a.owner_id: await _owner_or_400(a.owner_id, db) for a in data.actions}

        inspection = Inspection(
            subject_type=data.subject_type,
            vessel_id=data.vessel_id if data.subject_type == "vessel" else None,
            truck_id=data.truck_id if data.subject_type == "truck" else None,
            operation_id=operation.id if operation else None,
            site_name=data.site_name if data.subject_type == "site" else None,
            inspected_on=data.inspected_on,
            title=data.title,
            result=data.result,
            findings=data.findings,
            inspector_id=current_user.id,
            attachments=[],
        )
        db.add(inspection)
        await db.flush()

        db.add(_audit(
            current_user, "LOG_INSPECTION", "inspection", inspection.id,
            {
                "subject_type": data.subject_type,
                "subject": subject,
                "title": data.title,
                "result": data.result,
                "actions": len(data.actions),
            },
            request_meta, operation_id=inspection.operation_id,
        ))
        for a in data.actions:
            await InspectionService._add_action(inspection, a, owners[a.owner_id], current_user, db, request_meta)
        return await InspectionService._reload(inspection.id, db)

    @staticmethod
    async def _add_action(
        inspection: Inspection,
        data: InspectionActionCreate,
        owner: User,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict],
    ) -> InspectionAction:
        action = InspectionAction(
            inspection_id=inspection.id,
            description=data.description,
            owner_id=owner.id,
            due_date=data.due_date,
            status="open",
            created_by=current_user.id,
        )
        db.add(action)
        await db.flush()
        db.add(_audit(
            current_user, "ADD_INSPECTION_ACTION", "inspection_action", action.id,
            {
                "inspection": inspection.title,
                "description": data.description,
                "owner": owner.full_name,
                "due_date": data.due_date.isoformat(),
            },
            request_meta, operation_id=inspection.operation_id,
        ))
        if owner.id != current_user.id:
            # Only roles that can open the inspections page get a link to it.
            await notify(
                db=db,
                user_id=owner.id,
                type_="system",
                title="Safety action assigned to you",
                message=(
                    f"{current_user.full_name} asked you to fix this by "
                    f"{data.due_date.strftime('%d %b %Y')}: {data.description} "
                    f"(inspection: {inspection.title})."
                ),
                operation_id=inspection.operation_id,
                action_url="/safety/inspections" if owner.role in INSPECTION_VIEWER_ROLES else None,
                channels=["in_app"],
            )
        return action

    @staticmethod
    async def update_inspection(
        inspection_id: UUID,
        data: InspectionUpdate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        inspection = await InspectionService._get(inspection_id, db)
        update = data.model_dump(exclude_unset=True, exclude={"reason"})
        _refuse_blanked(update, _INSPECTION_REQUIRED)
        if "site_name" in update and inspection.subject_type != "site":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a site inspection has a site name")
        if inspection.subject_type == "site" and "site_name" in update and not update["site_name"]:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Site name cannot be left empty")
        if update.get("inspected_on") and update["inspected_on"] > date.today():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An inspection cannot be dated in the future")
        result = update.get("result", inspection.result)
        findings = update["findings"] if "findings" in update else inspection.findings
        if result != "pass" and not findings:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Write down what was found")

        changes = capture_diff(inspection, update)
        if not changes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing was changed")
        inspection.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "EDIT_INSPECTION", "inspection", inspection.id,
            changes, request_meta, operation_id=inspection.operation_id, reason=data.reason,
        ))
        return await InspectionService._reload(inspection.id, db)

    @staticmethod
    async def add_action(
        inspection_id: UUID,
        data: InspectionActionCreate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        inspection = await InspectionService._get(inspection_id, db)
        owner = await _owner_or_400(data.owner_id, db)
        await InspectionService._add_action(inspection, data, owner, current_user, db, request_meta)
        inspection.updated_at = datetime.utcnow()
        return await InspectionService._reload(inspection.id, db)

    @staticmethod
    async def update_action(
        action_id: UUID,
        data: InspectionActionUpdate,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        action = await InspectionService._get_action(action_id, db)
        if action.status == "done":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A finished action cannot be edited. Reopen it first.",
            )
        update = data.model_dump(exclude_unset=True, exclude={"reason"})
        _refuse_blanked(update, _ACTION_REQUIRED)
        if update.get("owner_id"):
            await _owner_or_400(update["owner_id"], db)
        changes = capture_diff(action, update)
        if not changes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing was changed")
        action.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "EDIT_INSPECTION_ACTION", "inspection_action", action.id,
            changes, request_meta, operation_id=action.inspection.operation_id, reason=data.reason,
        ))
        return await InspectionService._reload(action.inspection_id, db)

    @staticmethod
    async def complete_action(
        action_id: UUID,
        data: InspectionActionComplete,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        action = await InspectionService._get_action(action_id, db)
        if action.status == "done":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This action is already done")
        action.status = "done"
        action.done_note = data.note
        action.done_by = current_user.id
        action.done_at = datetime.utcnow()
        action.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "COMPLETE_INSPECTION_ACTION", "inspection_action", action.id,
            {"status": {"from": "open", "to": "done"}, "note": data.note},
            request_meta, operation_id=action.inspection.operation_id,
        ))
        return await InspectionService._reload(action.inspection_id, db)

    @staticmethod
    async def reopen_action(
        action_id: UUID,
        data: InspectionActionReopen,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Back to open. The old note stays on the audit log."""
        action = await InspectionService._get_action(action_id, db)
        if action.status != "done":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a finished action can be reopened")
        db.add(_audit(
            current_user, "REOPEN_INSPECTION_ACTION", "inspection_action", action.id,
            {
                "status": {"from": "done", "to": "open"},
                "previous_note": action.done_note,
                "previously_done_by": str(action.done_by) if action.done_by else None,
                "previously_done_at": action.done_at.isoformat() if action.done_at else None,
            },
            request_meta, operation_id=action.inspection.operation_id, reason=data.reason,
        ))
        action.status = "open"
        action.done_note = None
        action.done_by = None
        action.done_at = None
        action.updated_at = datetime.utcnow()
        return await InspectionService._reload(action.inspection_id, db)

    @staticmethod
    async def add_attachment(
        inspection_id: UUID,
        file: UploadFile,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Dict[str, Any]:
        inspection = await InspectionService._get(inspection_id, db)
        item = await _store_attachment(file, f"inspections/{inspection.id}", current_user)
        # A new list, not an in-place append — JSONB changes are only seen on reassignment.
        inspection.attachments = [*(inspection.attachments or []), item]
        inspection.updated_at = datetime.utcnow()
        db.add(_audit(
            current_user, "ADD_INSPECTION_ATTACHMENT", "inspection", inspection.id,
            {"file_name": item["file_name"], "attachment_id": item["id"]},
            request_meta, operation_id=inspection.operation_id,
        ))
        return await InspectionService._reload(inspection.id, db)

    @staticmethod
    async def attachment_link(inspection_id: UUID, attachment_id: str, db: AsyncSession) -> dict:
        inspection = await InspectionService._get(inspection_id, db)
        return await _attachment_link(inspection.attachments, attachment_id)
