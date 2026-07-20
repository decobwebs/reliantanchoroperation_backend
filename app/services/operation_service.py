from typing import List, Optional, Tuple
from datetime import datetime
from uuid import UUID
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.operation import Operation, OperationStatusHistory, TaskAssignment
from app.models.bdn import VesselActivity
from app.models.finance import PFI
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import OperationStatus, OperationType, UserRole
from app.schemas.operation import (
    CreateOperationRequest, UpdateOperationRequest, OperationFilters,
    TransitionRequest, ReopenRequest,
)
from app.services.state_machine import StateMachine, StateMachineError
from app.services.milestone_service import create_milestone_if_applicable
from app.services.audit_diff import capture_diff
from app.utils.number_generator import generate_operation_number


def _write_history(
    db: AsyncSession,
    operation: Operation,
    from_status: Optional[OperationStatus],
    to_status: OperationStatus,
    changed_by: UUID,
    reason: str = "",
) -> None:
    db.add(OperationStatusHistory(
        operation_id=operation.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        reason=reason,
        metadata_={},
    ))


async def _notify_all_finance(
    db: AsyncSession,
    operation: Operation,
    title: str,
    message: str,
) -> None:
    from app.services.notification_service import notify
    result = await db.execute(
        select(User).where(and_(User.role == UserRole.finance_manager, User.is_active == True))
    )
    for fm in result.scalars().all():
        await notify(
            db=db, user_id=fm.id,
            type_="operation_active",
            title=title, message=message,
            priority="high",
            operation_id=operation.id,
            action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"],
            wa_template="operation_update",
            wa_kwargs={"operation_number": operation.operation_number, "status": message},
        )


async def _notify_assigned_users(
    db: AsyncSession,
    operation: Operation,
    title: str,
    message: str,
    type_: str = "system",
    priority: str = "normal",
) -> None:
    from app.services.notification_service import notify
    result = await db.execute(
        select(TaskAssignment).where(TaskAssignment.operation_id == operation.id)
    )
    for ta in result.scalars().all():
        await notify(
            db=db, user_id=ta.assigned_to,
            type_=type_,
            title=title, message=message,
            priority=priority,
            operation_id=operation.id,
            action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"],
            wa_template="operation_update",
            wa_kwargs={"operation_number": operation.operation_number, "status": message},
        )


class OperationService:

    @staticmethod
    async def create_operation(
        data: CreateOperationRequest,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Operation:
        from app.services.notification_service import notify

        operation_number = await generate_operation_number(db)

        operation = Operation(
            operation_number=operation_number,
            type=data.type,
            status=OperationStatus.draft,
            client_id=data.client_id,
            created_by=current_user.id,
            expected_volume_mt=data.expected_volume_mt,
            product_type=data.product_type.value if data.product_type else None,
            loading_location=data.loading_location,
            discharge_location=data.discharge_location,
            notes=data.notes,
            currency=data.currency,
            vessel_id=data.vessel_id,
            version=1,
        )
        db.add(operation)
        await db.flush()

        _write_history(db, operation, None, OperationStatus.draft, current_user.id, "Operation created")

        db.add(AuditLog(
            user_id=current_user.id,
            operation_id=operation.id,
            action="CREATE_OPERATION",
            entity_type="operation",
            entity_id=operation.id,
            changes={"operation_number": operation_number, "type": data.type.value, "product": data.product_type.value if data.product_type else None},
            ip_address=request_meta.get("ip") if request_meta else None,
            user_agent=request_meta.get("user_agent") if request_meta else None,
        ))

        # ── One-step: assignments + auto-advance ───────────────────────────────
        if data.assignments:
            for a in data.assignments:
                db.add(TaskAssignment(
                    operation_id=operation.id,
                    assigned_to=a.assigned_to,
                    assigned_by=current_user.id,
                    task_type=a.task_type,
                    priority=a.priority,
                    instructions=a.instructions,
                    due_date=a.due_date,
                ))
                await notify(
                    db=db, user_id=a.assigned_to, type_="task_assigned",
                    title=f"Task Assigned — {operation_number}",
                    message=f"You have been assigned a {a.task_type.value.replace('_', ' ')} task on operation {operation_number}.",
                    priority=a.priority.value,
                    operation_id=operation.id,
                    action_url=f"/operations/{operation.id}",
                    channels=["in_app", "whatsapp"],
                    wa_template="task_assigned",
                    wa_kwargs={"operation_number": operation_number, "task_type": a.task_type.value},
                )

            await db.flush()

            # tasks_assigned
            _write_history(db, operation, OperationStatus.draft, OperationStatus.tasks_assigned, current_user.id, "Tasks assigned at creation")
            operation.status = OperationStatus.tasks_assigned
            await db.flush()

            if data.type == OperationType.vessel_only:
                # Vessel-only: no feedback needed — go directly active
                _write_history(db, operation, OperationStatus.tasks_assigned, OperationStatus.active, current_user.id, "Vessel-only operation activated")
                operation.status = OperationStatus.active
                await _notify_assigned_users(db, operation, f"Operation {operation_number} Active", f"Operation {operation_number} is now active.", "operation_active", "high")
                await _notify_all_finance(db, operation, f"Operation {operation_number} — Finance Required", f"Operation {operation_number} ({data.product_type.value if data.product_type else 'N/A'}) is active. Prepare PFI and payment docs.")
            else:
                # Truck/Full: await logistics feedback
                _write_history(db, operation, OperationStatus.tasks_assigned, OperationStatus.awaiting_feedback, current_user.id, "Awaiting truck readiness feedback")
                operation.status = OperationStatus.awaiting_feedback

        # ── PFI-first flow: link a pre-existing paid PFI ──────────────────────
        if data.pfi_id:
            from app.services.pfi_service import PfiService
            await PfiService.link_pfi_to_operation(data.pfi_id, operation.id, current_user, db)

        operation.updated_at = datetime.utcnow()
        await db.flush()
        await db.refresh(operation)
        return operation

    @staticmethod
    async def get_operation(
        operation_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> Operation:
        stmt = (
            select(Operation)
            .options(
                selectinload(Operation.client),
                selectinload(Operation.creator),
                selectinload(Operation.status_history),
                selectinload(Operation.task_assignments),
            )
            .where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        result = await db.execute(stmt)
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        OperationService._check_visibility(operation, current_user)
        return operation

    @staticmethod
    def _check_visibility(operation: Operation, user: User) -> None:
        if user.role in (UserRole.bunker_manager, UserRole.finance_manager):
            return
        if user.role == UserRole.client:
            if operation.client_id != user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
            return
        if hasattr(operation, "task_assignments") and operation.task_assignments is not None:
            assigned_ids = {ta.assigned_to for ta in operation.task_assignments}
            if user.id not in assigned_ids:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    @staticmethod
    async def list_operations(
        filters: OperationFilters,
        current_user: User,
        db: AsyncSession,
    ) -> Tuple[List[Operation], int]:
        conditions = [Operation.deleted_at.is_(None)]
        if current_user.role == UserRole.client:
            conditions.append(Operation.client_id == current_user.id)
        elif current_user.role in (UserRole.bunker_manager, UserRole.finance_manager):
            pass  # see all operations
        elif current_user.role == UserRole.marine_manager:
            # Marine managers see operations via vessel activity assignments (not tasks)
            task_op_ids_stmt = select(TaskAssignment.operation_id).where(TaskAssignment.assigned_to == current_user.id)
            va_op_ids_stmt = select(VesselActivity.operation_id).where(VesselActivity.assigned_to == current_user.id)
            task_result = await db.execute(task_op_ids_stmt)
            va_result = await db.execute(va_op_ids_stmt)
            assigned_op_ids = list({row[0] for row in task_result.fetchall()} | {row[0] for row in va_result.fetchall()})
            conditions.append(Operation.id.in_(assigned_op_ids))
        else:
            # task-scoped roles: logistics_officer, ops_supervisor
            assigned_op_ids_stmt = select(TaskAssignment.operation_id).where(TaskAssignment.assigned_to == current_user.id)
            assigned_result = await db.execute(assigned_op_ids_stmt)
            assigned_op_ids = [row[0] for row in assigned_result.fetchall()]
            conditions.append(Operation.id.in_(assigned_op_ids))

        if filters.status:
            conditions.append(Operation.status == filters.status)
        if filters.type:
            conditions.append(Operation.type == filters.type)
        if filters.client_id:
            conditions.append(Operation.client_id == filters.client_id)
        if filters.date_from:
            conditions.append(Operation.created_at >= filters.date_from)
        if filters.date_to:
            conditions.append(Operation.created_at <= filters.date_to)

        count_result = await db.execute(select(func.count()).select_from(Operation).where(and_(*conditions)))
        total = count_result.scalar_one()

        offset = (filters.page - 1) * filters.per_page
        stmt = (
            select(Operation)
            .where(and_(*conditions))
            .order_by(Operation.created_at.desc())
            .offset(offset)
            .limit(filters.per_page)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def update_operation(
        operation_id: UUID,
        data: UpdateOperationRequest,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Operation:
        result = await db.execute(
            select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        changes = capture_diff(operation, update_data)

        operation.updated_at = datetime.utcnow()
        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id,
            action="UPDATE_OPERATION", entity_type="operation", entity_id=operation.id,
            changes=changes,
            reason=data.reason,
            ip_address=request_meta.get("ip") if request_meta else None,
            user_agent=request_meta.get("user_agent") if request_meta else None,
        ))
        await db.flush()
        await db.refresh(operation)
        return operation

    @staticmethod
    async def transition_operation(
        operation_id: UUID,
        data: TransitionRequest,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Operation:
        result = await db.execute(
            select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        try:
            StateMachine.validate_transition(operation.type, operation.status, data.to_status, current_user.role)
        except StateMachineError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

        # ── Phase 1 Activation Gate ────────────────────────────────────────────
        # For vessel and full operations, a PFI must be linked before activation.
        if (
            data.to_status == OperationStatus.active
            and operation.type != OperationType.truck_only
        ):
            pfi_count_result = await db.execute(
                select(func.count()).select_from(PFI).where(PFI.operation_id == operation.id)
            )
            if (pfi_count_result.scalar() or 0) == 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="A PFI (Proforma Invoice) must be linked to this operation before it can be activated.",
                )

        from_status = operation.status
        operation.status = data.to_status
        operation.updated_at = datetime.utcnow()

        if data.to_status == OperationStatus.completed:
            operation.completed_at = datetime.utcnow()
            if data.completion_notes:
                operation.completion_notes = data.completion_notes

        _write_history(db, operation, from_status, data.to_status, current_user.id, data.reason or "")

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id,
            action="TRANSITION_OPERATION", entity_type="operation", entity_id=operation.id,
            changes={"from_status": from_status.value, "to_status": data.to_status.value, "reason": data.reason},
            ip_address=request_meta.get("ip") if request_meta else None,
            user_agent=request_meta.get("user_agent") if request_meta else None,
        ))

        await create_milestone_if_applicable(db, operation.id, data.to_status)

        # ── Post-transition notifications ──────────────────────────────────────
        from app.services.notification_service import notify

        if data.to_status == OperationStatus.active:
            await _notify_assigned_users(
                db, operation,
                f"Operation {operation.operation_number} is Now Active",
                f"Operation {operation.operation_number} is ACTIVE. Proceed with assigned tasks.",
                "operation_active", "high",
            )
            await _notify_all_finance(
                db, operation,
                f"Operation {operation.operation_number} — Finance Action Required",
                f"Operation {operation.operation_number} is active. Finance: prepare PFI and payment documentation.",
            )

        elif data.to_status == OperationStatus.pending_completion:
            bm_result = await db.execute(select(User).where(and_(User.role == UserRole.bunker_manager, User.is_active == True)))
            for bm in bm_result.scalars().all():
                await notify(
                    db=db, user_id=bm.id, type_="completion_pending",
                    title=f"Completion Report — {operation.operation_number}",
                    message=f"Supervisor submitted completion for {operation.operation_number}. Review and close.",
                    priority="high", operation_id=operation.id,
                    action_url=f"/operations/{operation.id}",
                    channels=["in_app", "whatsapp"],
                    wa_template="operation_update",
                    wa_kwargs={"operation_number": operation.operation_number, "status": "Completion pending review"},
                )

        elif data.to_status == OperationStatus.completed:
            await _notify_assigned_users(
                db, operation,
                f"Operation {operation.operation_number} Completed",
                f"Operation {operation.operation_number} has been completed and closed by the Bunker Manager.",
                "approved", "normal",
            )

        await db.flush()
        await db.refresh(operation)
        return operation

    @staticmethod
    async def reopen_operation(
        operation_id: UUID,
        data: ReopenRequest,
        current_user: User,
        db: AsyncSession,
        request_meta: Optional[dict] = None,
    ) -> Operation:
        """Create a new revision of a completed/archived/cancelled operation."""
        result = await db.execute(
            select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
        )
        parent = result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        terminal = {OperationStatus.completed, OperationStatus.archived, OperationStatus.cancelled}
        if parent.status not in terminal:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only completed, archived, or cancelled operations can be reopened (current: {parent.status.value})",
            )

        root_id = parent.parent_operation_id or parent.id

        count_result = await db.execute(
            select(func.count()).select_from(Operation).where(
                or_(Operation.id == root_id, Operation.parent_operation_id == root_id)
            )
        )
        existing = count_result.scalar_one()
        new_version = existing + 1

        root_result = await db.execute(select(Operation).where(Operation.id == root_id))
        root = root_result.scalar_one()
        base_number = root.operation_number.split("-Rv")[0]
        new_number = f"{base_number}-Rv{new_version}"

        new_op = Operation(
            operation_number=new_number,
            type=parent.type,
            status=OperationStatus.tasks_assigned,
            client_id=parent.client_id,
            created_by=current_user.id,
            expected_volume_mt=parent.expected_volume_mt,
            product_type=parent.product_type,
            currency=parent.currency,
            vessel_id=parent.vessel_id,
            notes=parent.notes,
            version=new_version,
            parent_operation_id=root_id,
            version_notes=data.version_notes,
        )
        db.add(new_op)
        await db.flush()

        _write_history(db, new_op, None, OperationStatus.tasks_assigned, current_user.id, f"Revision {new_version}: {data.version_notes}")
        db.add(AuditLog(
            user_id=current_user.id, operation_id=new_op.id,
            action="REOPEN_OPERATION", entity_type="operation", entity_id=new_op.id,
            changes={"parent_id": str(root_id), "version": new_version, "reason": data.version_notes},
            ip_address=request_meta.get("ip") if request_meta else None,
            user_agent=request_meta.get("user_agent") if request_meta else None,
        ))

        await db.flush()
        await db.refresh(new_op)
        return new_op

    @staticmethod
    async def pause_operation(operation_id: UUID, reason: str, current_user: User, db: AsyncSession, request_meta: Optional[dict] = None) -> Operation:
        result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        if operation.paused_at is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation is already paused")
        terminal = {OperationStatus.completed, OperationStatus.archived, OperationStatus.cancelled}
        if operation.status in terminal:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot pause a terminal operation")
        operation.paused_at = datetime.utcnow()
        operation.paused_reason = reason
        operation.updated_at = datetime.utcnow()
        db.add(AuditLog(user_id=current_user.id, operation_id=operation.id, action="PAUSE_OPERATION", entity_type="operation", entity_id=operation.id, changes={"reason": reason}, ip_address=request_meta.get("ip") if request_meta else None, user_agent=request_meta.get("user_agent") if request_meta else None))
        await db.flush()
        await db.refresh(operation)
        return operation

    @staticmethod
    async def resume_operation(operation_id: UUID, reason: Optional[str], current_user: User, db: AsyncSession, request_meta: Optional[dict] = None) -> Operation:
        result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        if operation.paused_at is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation is not paused")
        operation.paused_at = None
        operation.paused_reason = None
        operation.updated_at = datetime.utcnow()
        db.add(AuditLog(user_id=current_user.id, operation_id=operation.id, action="RESUME_OPERATION", entity_type="operation", entity_id=operation.id, changes={"reason": reason}, ip_address=request_meta.get("ip") if request_meta else None, user_agent=request_meta.get("user_agent") if request_meta else None))
        await db.flush()
        await db.refresh(operation)
        return operation

    @staticmethod
    async def soft_delete_operation(operation_id: UUID, current_user: User, db: AsyncSession, request_meta: Optional[dict] = None) -> None:
        result = await db.execute(select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None))))
        operation = result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
        if operation.status != OperationStatus.draft:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only draft operations can be deleted")
        operation.deleted_at = datetime.utcnow()
        operation.updated_at = datetime.utcnow()
        db.add(AuditLog(user_id=current_user.id, operation_id=operation.id, action="DELETE_OPERATION", entity_type="operation", entity_id=operation.id, changes={"deleted_at": operation.deleted_at.isoformat()}, ip_address=request_meta.get("ip") if request_meta else None, user_agent=request_meta.get("user_agent") if request_meta else None))
        await db.flush()

    @staticmethod
    async def get_timeline(operation_id: UUID, current_user: User, db: AsyncSession) -> List[OperationStatusHistory]:
        await OperationService.get_operation(operation_id, current_user, db)
        stmt = select(OperationStatusHistory).where(OperationStatusHistory.operation_id == operation_id).order_by(OperationStatusHistory.created_at.asc())
        result = await db.execute(stmt)
        return list(result.scalars().all())
