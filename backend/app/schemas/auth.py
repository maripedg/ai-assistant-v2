from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserPublic(BaseModel):
    id: int
    email: EmailStr
    role: str
    status: str


class LoginResponse(BaseModel):
    token: str
    user: UserPublic

