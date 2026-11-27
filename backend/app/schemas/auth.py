from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["admin", "editor", "viewer"]


def _check_password(v: str) -> str:
    if len(v) < 12:
        raise ValueError("Password must be at least 12 characters")
    if len(v) > 128:
        raise ValueError("Password must be at most 128 characters")
    return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class SetupStatus(BaseModel):
    setup_required: bool


class SetupAdminIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str

    _pw = field_validator("password")(_check_password)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AuthOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    password: str

    _pw = field_validator("password")(_check_password)


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = "editor"


class InviteOut(BaseModel):
    email: str
    role: Role
    invite_token: str
    expires_at: datetime


class UserUpdateIn(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
