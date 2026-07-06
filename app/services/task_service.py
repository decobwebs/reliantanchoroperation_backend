from typing import List, Optional
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.operation import Operation, TaskAssignment
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, TaskStatus, OperationStatus
from app.schemas.task import TaskAssignmentCreate, TaskAssignmentUpdate
from app.services.notification_service import notify


class TaskService:

    @staticmethod
    async def create_task(
        operation_id: UUID,
        data: TaskAssignmentCreate,
        current_user: User,
        db: AsyncSession,
    ) -> TaskAssignment:
        # Verify operation exists
        op_result = await db.execute(
            select(Operation).where(
                and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
            )
        )
        operation = op_result.scalar_one_or_none()
        if not operation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        # Verify assigned user exists
        user_result = await db.execute(
            select(User).where(User.id == data.assigned_to)
        )
        assignee = user_result.scalar_one_or_none()
        if not assignee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assigned user not found",
            )

        # Prevent duplicate: same user + task_type on same operation while non-terminal
        dup_result = await db.execute(
            select(TaskAssignment).where(
                and_(
                    TaskAssignment.operation_id == operation_id,
                    TaskAssignment.assigned_to == data.assigned_to,
                    TaskAssignment.task_type == data.task_type,
                    TaskAssignment.status.not_in([TaskStatus.cancelled, TaskStatus.completed]),
                )
            )
        )
        if dup_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"An active {data.task_type.value.replace('_', ' ')} task is already "
                    f"assigned to this user for this operation."
                ),
            )

        task = TaskAssignment(
            operation_id=operation_id,
            assigned_to=data.assigned_to,
            assigned_by=current_user.id,
            task_type=data.task_type,
            priority=data.priority,
            instructions=data.instructions,
            due_date=data.due_date,
            status=TaskStatus.pending,
        )
        db.add(task)
        await db.flush()

        # Notification to assignee (in-app + WhatsApp)
        await notify(
            db=db,
            user_id=data.assigned_to,
            type_="task_assigned",
            title="New Task Assigned",
            message=f"You have been assigned a {data.task_type.value} task for operation {operation.operation_number}",
            priority=data.priority.value,
            operation_id=operation_id,
            action_url=f"/operations/{operation_id}/tasks/{task.id}",
            channels=["in_app", "whatsapp"],
            wa_template="task_assigned",
            wa_kwargs={
                "operation_number": operation.operation_number,
                "task_type": data.task_type.value.replace("_", " ").title(),
                "priority": data.priority.value.upper(),
            },
        )

        # Audit log
        audit = AuditLog(
            user_id=current_user.id,
            operation_id=operation_id,
            action="CREATE_TASK",
            entity_type="task_assignment",
            entity_id=task.id,
            changes={
                "task_type": data.task_type.value,
                "assigned_to": str(data.assigned_to),
                "priority": data.priority.value,
            },
        )
        db.add(audit)

        await db.flush()
        await db.refresh(task)

        # Load relationships
        result = await db.execute(
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
                selectinload(TaskAssignment.operation),
            )
            .where(TaskAssignment.id == task.id)
        )
        return result.scalar_one()

    @staticmethod
    async def list_tasks(
        operation_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> List[TaskAssignment]:
        # Verify operation exists
        op_result = await db.execute(
            select(Operation).where(
                and_(Operation.id == operation_id, Operation.deleted_at.is_(None))
            )
        )
        if not op_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")

        conditions = [TaskAssignment.operation_id == operation_id]

        # Non-BM users only see their own tasks
        if current_user.role != UserRole.bunker_manager:
            conditions.append(TaskAssignment.assigned_to == current_user.id)

        stmt = (
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
                selectinload(TaskAssignment.operation),
            )
            .where(and_(*conditions))
            .order_by(TaskAssignment.created_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_task(
        task_id: UUID,
        data: TaskAssignmentUpdate,
        current_user: User,
        db: AsyncSession,
    ) -> TaskAssignment:
        result = await db.execute(
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
            )
            .where(TaskAssignment.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

        # Only assignee or BM can update
        if (
            current_user.role != UserRole.bunker_manager
            and task.assigned_to != current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to update this task",
            )

        update_data = data.model_dump(exclude_unset=True)
        changes = {}
        for field, value in update_data.items():
            old_val = getattr(task, field, None)
            changes[field] = {"from": str(old_val), "to": str(value)}
            setattr(task, field, value)

        # Mark completed_at if status moves to completed
        if data.status == TaskStatus.completed and not task.completed_at:
            task.completed_at = datetime.utcnow()

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=task.operation_id,
            action="UPDATE_TASK",
            entity_type="task_assignment",
            entity_id=task.id,
            changes=changes,
        )
        db.add(audit)

        await db.flush()
        final = await db.execute(
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
                selectinload(TaskAssignment.operation),
            )
            .where(TaskAssignment.id == task_id)
        )
        return final.scalar_one()

    @staticmethod
    async def cancel_task(
        task_id: UUID,
        current_user: User,
        db: AsyncSession,
    ) -> TaskAssignment:
        result = await db.execute(
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
            )
            .where(TaskAssignment.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

        if task.status == TaskStatus.cancelled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is already cancelled",
            )

        task.status = TaskStatus.cancelled

        audit = AuditLog(
            user_id=current_user.id,
            operation_id=task.operation_id,
            action="CANCEL_TASK",
            entity_type="task_assignment",
            entity_id=task.id,
            changes={"status": {"from": task.status.value, "to": "cancelled"}},
        )
        db.add(audit)

        await db.flush()
        final = await db.execute(
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
                selectinload(TaskAssignment.operation),
            )
            .where(TaskAssignment.id == task_id)
        )
        return final.scalar_one()

    @staticmethod
    async def get_my_tasks(
        current_user: User,
        db: AsyncSession,
    ) -> List[TaskAssignment]:
        stmt = (
            select(TaskAssignment)
            .options(
                selectinload(TaskAssignment.assignee),
                selectinload(TaskAssignment.assigner),
                selectinload(TaskAssignment.operation),
            )
            .where(TaskAssignment.assigned_to == current_user.id)
            .order_by(TaskAssignment.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
