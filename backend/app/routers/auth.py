from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import jwt_secret, jwt_ttl_min, usage_log_enabled
from backend.app.core.security import issue_jwt, decode_jwt, verify_password
from backend.app.schemas.auth import LoginRequest, LoginResponse, UserPublic
from backend.core.db.session import get_db, session_scope
from backend.core.models.users import User
from backend.core.repos.usage_repo_db import UsageRepoDB


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email: EmailStr = payload.email
    # Case-insensitive lookup
    stmt = select(User).where(func.lower(User.email) == func.lower(str(email)))
    user: Optional[User] = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")
    status = (getattr(user, "status", None) or "").lower()
    if status in {"suspended", "deleted"}:
        raise HTTPException(status_code=403, detail="forbidden")
    if not getattr(user, "password_hash", None) or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="unauthorized")

    token = issue_jwt(user_id=user.id, email=user.email, role=user.role or "user", ttl_min=jwt_ttl_min(), secret=jwt_secret())
    response_payload = LoginResponse(
        token=token,
        user=UserPublic(id=user.id, email=user.email, role=user.role or "user", status=user.status or "active"),
    )
    if usage_log_enabled():
        client_label, ui_version = _resolve_client_headers(request)
        ip = _extract_ip(request)
        user_agent = request.headers.get("user-agent")
        try:
            with session_scope() as usage_db:
                UsageRepoDB.log_login(
                    usage_db,
                    user_id=int(user.id) if user.id is not None else None,
                    email=user.email,
                    client=client_label,
                    ui_version=ui_version,
                    ip=ip,
                    user_agent=user_agent,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("usage.log_login skipped (%s)", exc.__class__.__name__)
    return response_payload


@router.post("/refresh", response_model=LoginResponse)
def refresh(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_jwt(token, secret=jwt_secret())
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="invalid_token")
    user_id = int(payload.get("sub"))
    user: Optional[User] = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    status = (user.status or "").lower()
    if status in {"suspended", "deleted"}:
        raise HTTPException(status_code=403, detail="forbidden")
    new_token = issue_jwt(user_id=user.id, email=user.email, role=user.role or "user", ttl_min=jwt_ttl_min(), secret=jwt_secret())
    return LoginResponse(token=new_token, user=UserPublic(id=user.id, email=user.email, role=user.role or "user", status=user.status or "active"))


def _resolve_client_headers(request: Request) -> tuple[str, Optional[str]]:
    headers = request.headers
    client = headers.get("x-client-app") or "streamlit"
    ui_version = headers.get("x-ui-version") or headers.get("x-app-version") or "chat-v2"
    return str(client), ui_version


def _extract_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    client = request.client
    return getattr(client, "host", None)
