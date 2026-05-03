from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.task import TaskAssignmentCreate, TaskAssignmentUpdate, TaskAssignmentOut
from app.services.task_service import TaskService

router = APIRouter(tags=["Tasks"])


@router.get("/operations/{operation_id}/tasks", response_model=StandardResponse)
async def list_tasks(
    operation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List tasks for an operation. BM sees all; other roles see only their own."""
    tasks = await TaskService.list_tasks(operation_id, current_user, db)
    items = [TaskAssignmentOut.model_validate(t).model_dump() for t in tasks]
    return StandardResponse.ok(data=items, message="Tasks retrieved")


@router.post(
    "/operations/{operation_id}/tasks",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    operation_id: UUID,
    body: TaskAssignmentCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Create a task assignment for an operation. Bunker Manager only."""
    task = await TaskService.create_task(operation_id, body, current_user, db)
    return StandardResponse.ok(
        data=TaskAssignmentOut.model_validate(task).model_dump(),
        message="Task created",
    )


@router.put("/tasks/{task_id}", response_model=StandardResponse)
async def update_task(
    task_id: UUID,
    body: TaskAssignmentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a task. Only the assigned user or Bunker Manager can update."""
    task = await TaskService.update_task(task_id, body, current_user, db)
    return StandardResponse.ok(
        data=TaskAssignmentOut.model_validate(task).model_dump(),
        message="Task updated",
    )


@router.delete("/tasks/{task_id}", response_model=StandardResponse)
async def cancel_task(
    task_id: UUID,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a task. Bunker Manager only."""
    task = await TaskService.cancel_task(task_id, current_user, db)
    return StandardResponse.ok(
        data=TaskAssignmentOut.model_validate(task).model_dump(),
        message="Task cancelled",
    )


@router.get("/my-tasks", response_model=StandardResponse)
async def get_my_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all tasks assigned to the current user across all operations."""
    tasks = await TaskService.get_my_tasks(current_user, db)
    items = [TaskAssignmentOut.model_validate(t).model_dump() for t in tasks]
    return StandardResponse.ok(data=items, message="My tasks retrieved")
