from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, field_validator
from app.models.enums import UserRole


class UserOut(BaseModel):
    id: UUID
    auth_id: UUID
    email: str
    full_name: str
    phone: Optional[str] = None
    role: UserRole
    acting_as_role: Optional[UserRole] = None
    is_active: bool
    avatar_url: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ActAsRequest(BaseModel):
    role: UserRole


class UserBrief(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class AdminCreateUserRequest(BaseModel):
    """No password field: the admin never sets or sees a user's password. The
    account is created with a random, unusable password and the recipient is
    emailed a link to set their own (see AuthService.generate_action_link)."""
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    role: UserRole

    @field_validator("full_name")
    @classmethod
    def full_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Full name cannot be empty")
        return v.strip()


class AdminUpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    avatar_url: Optional[str] = None
