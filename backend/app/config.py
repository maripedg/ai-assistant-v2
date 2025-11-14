from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable, Set

from dotenv import load_dotenv


logger = logging.getLogger(__name__)

_DOTENV_LOADED = False
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_DIR.parent


def _load_env_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return

    candidates = []
    env_hint = os.environ.get("APP_ENV_FILE")
    if env_hint:
        candidates.append(Path(env_hint))
    candidates.append(_PROJECT_ROOT / ".env")
    candidates.append(_BACKEND_DIR / ".env")

    for candidate in candidates:
        candidate = Path(candidate).expanduser()
        if not candidate.is_absolute():
            candidate = (_PROJECT_ROOT / candidate).resolve()
        if candidate.exists():
            load_dotenv(candidate)
            break

    _DOTENV_LOADED = True


_load_env_once()


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    if value is None:
        return default
    return value


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_int_or_none(key: str) -> int | None:
    raw = _env(key)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # Fallback to SESSION_SECRET if provided
        secret = os.getenv("SESSION_SECRET", "changeme-secret")
    return secret


def jwt_ttl_min() -> int:
    try:
        return int(os.getenv("JWT_TTL_MIN", "1440"))
    except Exception:
        return 1440


def jwt_alg() -> str:
    return os.getenv("JWT_ALG", "HS256")


def storage_backend() -> str:
    return _env("STORAGE_BACKEND", "local") or "local"


def staging_dir() -> str:
    return _env("STAGING_DIR", "/data/staging") or "/data/staging"


def allow_mime(default: Iterable[str] | None = None) -> Set[str]:
    if default is None:
        default = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/plain",
            "text/html",
        }
    raw = _env("ALLOW_MIME")
    if not raw:
        return {item.lower() for item in default}
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, (list, set, tuple)):
            return {str(item).lower() for item in parsed if str(item).strip()}
        if isinstance(parsed, str):
            raw = parsed
    except json.JSONDecodeError:
        pass
    items = [seg.strip() for seg in raw.split(",")]
    cleaned = {seg.lower() for seg in items if seg}
    return cleaned or {item.lower() for item in default}


def max_upload_mb() -> int:
    return _env_int("MAX_UPLOAD_MB", 100)


def max_upload_bytes() -> int:
    return max(1, max_upload_mb()) * 1024 * 1024


def sp_sync_base_url() -> str:
    return _env("SP_SYNC_BASE_URL", "http://localhost:5030") or "http://localhost:5030"


def sp_site_key() -> str:
    return _env("SP_SITE_KEY", "") or ""


def sp_download_dir() -> str:
    return _env("SP_DOWNLOAD_DIR", "/data/sharepoint/download") or "/data/sharepoint/download"


def embed_profile() -> str:
    return _env("EMBED_PROFILE", "multilingual_profile") or "multilingual_profile"


def embed_update_alias() -> bool:
    return _env_bool("EMBED_UPDATE_ALIAS", True)


def embed_evaluate() -> bool:
    return _env_bool("EMBED_EVALUATE", False)


def usage_log_enabled() -> bool:
    raw = os.getenv("USAGE_LOG_ENABLED", "true")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def sp_schedule_enabled() -> bool:
    return _env_bool("SP_SCHEDULE_ENABLED", True)


def sp_schedule_cron() -> str:
    return _env("SP_SCHEDULE_CRON", "0 3 * * *") or "0 3 * * *"


def sp_timezone() -> str:
    return _env("SP_TIMEZONE", "America/Bogota") or "America/Bogota"


EMBED_BATCH_SIZE = _env_int("EMBED_BATCH_SIZE", 32)
EMBED_WORKERS = _env_int("EMBED_WORKERS", 1)
EMBED_RATE_LIMIT_PER_MIN = _env_int_or_none("EMBED_RATE_LIMIT_PER_MIN")

logger.info(
    "config: EMBED_BATCH_SIZE=%s EMBED_WORKERS=%s EMBED_RATE_LIMIT_PER_MIN=%s",
    EMBED_BATCH_SIZE,
    EMBED_WORKERS,
    EMBED_RATE_LIMIT_PER_MIN,
)
