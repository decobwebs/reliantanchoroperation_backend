from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr
from app.models.enums import UserRole


class UserOut(BaseModel):
    id: UUID
    auth_id: UUID
    email: str
    full_name: str
    phone: Optional[str] = None
    role: UserRole
    is_active: bool
    avatar_url: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserBrief(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    phone: Optional[str] = None
    role: UserRole


class AdminUpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    avatar_url: Optional[str] = None
