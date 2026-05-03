from typing import Optional, Any
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, field_validator
from app.models.enums import TaskType, TaskStatus, Priority


class TaskAssignmentCreate(BaseModel):
    assigned_to: UUID
    task_type: TaskType
    priority: Priority = Priority.normal
    instructions: Optional[str] = None
    due_date: Optional[datetime] = None

    @field_validator("instructions", mode="before")
    @classmethod
    def strip_instructions(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class TaskAssignmentUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    priority: Optional[Priority] = None
    instructions: Optional[str] = None
    due_date: Optional[datetime] = None

    @field_validator("instructions", mode="before")
    @classmethod
    def strip_instructions(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v


class UserBriefOut(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str

    model_config = {"from_attributes": True}


class OperationBriefOut(BaseModel):
    id: UUID
    operation_number: str
    type: str
    status: str

    model_config = {"from_attributes": True}


class TaskAssignmentOut(BaseModel):
    id: UUID
    operation_id: UUID
    assigned_to: UUID
    assigned_by: UUID
    task_type: TaskType
    status: TaskStatus
    priority: Priority
    instructions: Optional[str] = None
    due_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    assignee: Optional[UserBriefOut] = None
    assigner: Optional[UserBriefOut] = None
    operation: Optional[OperationBriefOut] = None

    model_config = {"from_attributes": True}
