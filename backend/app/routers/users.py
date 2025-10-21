from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import os
from sqlalchemy.orm import Session

from backend.app.models.user import UserCreate, UserOut, UserUpdate, PasswordChange
from backend.app.deps import settings
from backend.core.db.session import get_db
from backend.core.repos.factory import get_users_repo
from backend.core.security.passwords import hash_password
from backend.core.security.passwords import verify_password
from datetime import datetime, timezone


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _max_limit(limit: int) -> int:
    return min(max(limit, 1), 100)


@router.post("/", response_model=UserOut)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    repo = get_users_repo(session=db)
    cfg = settings.app.get("auth", {}) if isinstance(settings.app, dict) else {}
    auth_mode = (cfg or {}).get("mode", "local")
    password_algo = (cfg or {}).get("password_algo", "bcrypt")
    # allow env override AUTH_REQUIRE_SIGNUP_APPROVAL=false|true
    env_override = os.getenv("AUTH_REQUIRE_SIGNUP_APPROVAL")
    if env_override is not None and env_override != "":
        require_approval = str(env_override).lower() not in {"false", "0", "no", "off"}
    else:
        require_approval = bool((cfg or {}).get("require_signup_approval", False))

    # Determine status precedence: request override > config flag
    new_status = (
        (payload.status if hasattr(payload, "status") else None)
        or ("invited" if require_approval else "active")
    )

    data = {
        "email": payload.email,
        "name": payload.name,
        "role": payload.role or "user",
        "status": new_status,
        "auth_provider": "local" if auth_mode == "local" else "sso",
    }
    if auth_mode == "local" and payload.password:
        data["password_algo"] = password_algo
        data["password_hash"] = hash_password(payload.password, password_algo)

    # Enforce unique email at app level too
    existing = None
    try:
        existing = repo.get_by_email(payload.email)
    except Exception:
        pass
    if existing:
        raise HTTPException(status_code=409, detail="email_already_exists")

    user = repo.create(data)
    logger.info("user.create email=%s role=%s status=%s", payload.email, data["role"], data["status"])
    return user


@router.get("/", response_model=List[UserOut])
def list_users(
    email: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    repo = get_users_repo(session=db)
    items, _ = repo.list(email=email, status=status, limit=_max_limit(limit), offset=offset)
    return items


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db)):
    repo = get_users_repo(session=db)
    user = repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
    repo = get_users_repo(session=db)
    data = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    user = repo.update(user_id, data)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")
    logger.info("user.update id=%s changes=%s", user_id, list(data.keys()))
    return user


@router.delete("/{user_id}")
def delete_user(user_id: int, hard: bool = False, db: Session = Depends(get_db)):
    repo = get_users_repo(session=db)
    ok = repo.delete(user_id, hard=hard)
    if not ok:
        raise HTTPException(status_code=404, detail="user_not_found")
    action = "hard_delete" if hard else "suspend"
    logger.info("user.%s id=%s", action, user_id)
    return {"ok": True}


@router.post("/{user_id}/password")
def change_password(user_id: int, payload: PasswordChange, db: Session = Depends(get_db)):
    cfg = settings.app.get("auth", {}) if isinstance(settings.app, dict) else {}
    if (cfg or {}).get("mode", "local") != "local":
        raise HTTPException(status_code=400, detail="local_auth_disabled")

    repo = get_users_repo(session=db)
    user = repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")

    # TODO: integrate real auth/roles; for now treat as admin in dev
    is_admin = True
    # If not admin, require current_password and verify
    if not is_admin:
        current = getattr(user, "password_hash", None) if hasattr(user, "password_hash") else (user.get("password_hash") if isinstance(user, dict) else None)
        algo = getattr(user, "password_algo", None) if hasattr(user, "password_algo") else (user.get("password_algo") if isinstance(user, dict) else None)
        if not payload.current_password or not current or not algo or not verify_password(payload.current_password, current, algo):
            raise HTTPException(status_code=401, detail="invalid_current_password")

    algo = (cfg or {}).get("password_algo", "bcrypt")
    new_hash = hash_password(payload.new_password, algo)

    # Persist
    if hasattr(repo, "update_password"):
        repo.update_password(user_id, new_hash, algo)
    else:
        # Fallback to generic update
        repo.update(user_id, {"password_hash": new_hash, "password_algo": algo, "password_updated_at": datetime.now(timezone.utc).isoformat()})

    logger.info("user.password_change id=%s", user_id)
    return {"ok": True}
