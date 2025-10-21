from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.app.deps import settings
from backend.core.db.session import SessionLocal
from backend.core.repos.users_repo_db import UsersRepoDB
from backend.core.repos.users_repo_json import UsersRepoJSON
from backend.core.repos.feedback_repo_db import FeedbackRepoDB
from backend.core.repos.feedback_repo_json import FeedbackRepoJSON


def _storage_cfg() -> Dict[str, Any]:
    return (settings.app.get("storage") or {}) if isinstance(settings.app, dict) else {}


@dataclass
class _DualUsers:
    primary: Any
    secondary: Any

    def create(self, data):
        out = self.primary.create(data)
        try:
            self.secondary.create(data)
        except Exception:
            pass
        return out

    def get(self, user_id: int):
        return self.primary.get(user_id)

    def get_by_email(self, email: str):
        return self.primary.get_by_email(email)

    def list(self, **kwargs):
        return self.primary.list(**kwargs)

    def update(self, user_id: int, data):
        out = self.primary.update(user_id, data)
        try:
            self.secondary.update(user_id, data)
        except Exception:
            pass
        return out

    def delete(self, user_id: int, *, hard: bool = False):
        ok = self.primary.delete(user_id, hard=hard)
        try:
            self.secondary.delete(user_id, hard=hard)
        except Exception:
            pass
        return ok


@dataclass
class _DualFeedback:
    primary: Any
    secondary: Any

    def create(self, data):
        out = self.primary.create(data)
        try:
            self.secondary.create(data)
        except Exception:
            pass
        return out

    def get(self, fb_id: int):
        return self.primary.get(fb_id)

    def list(self, **kwargs):
        return self.primary.list(**kwargs)


def get_users_repo(session=None):
    cfg = _storage_cfg()
    users_cfg = cfg.get("users", {})
    mode = users_cfg.get("mode", "db")
    dual = bool(cfg.get("dual_write", False))
    if mode == "json":
        primary = UsersRepoJSON(users_cfg.get("json_path", "data/users.json"))
        if dual:
            secondary = UsersRepoDB(session or SessionLocal())
            return _DualUsers(primary=primary, secondary=secondary)
        return primary
    # default: db
    repo = UsersRepoDB(session or SessionLocal())
    if dual:
        secondary = UsersRepoJSON(users_cfg.get("json_path", "data/users.json"))
        return _DualUsers(primary=repo, secondary=secondary)
    return repo


def get_feedback_repo(session=None):
    cfg = _storage_cfg()
    fb_cfg = cfg.get("feedback", {})
    mode = fb_cfg.get("mode", "db")
    dual = bool(cfg.get("dual_write", False))
    if mode == "json":
        primary = FeedbackRepoJSON(fb_cfg.get("json_path", "data/feedback.json"))
        if dual:
            secondary = FeedbackRepoDB(session or SessionLocal())
            return _DualFeedback(primary=primary, secondary=secondary)
        return primary
    repo = FeedbackRepoDB(session or SessionLocal())
    if dual:
        secondary = FeedbackRepoJSON(fb_cfg.get("json_path", "data/feedback.json"))
        return _DualFeedback(primary=repo, secondary=secondary)
    return repo

