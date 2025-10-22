from __future__ import annotations

import json
import os
from typing import Iterable, Set


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
    return os.getenv("STORAGE_BACKEND", "local").lower()


def staging_dir() -> str:
    return os.getenv("STAGING_DIR", "/data/staging")


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
    raw = os.getenv("ALLOW_MIME")
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
    try:
        return int(os.getenv("MAX_UPLOAD_MB", "100"))
    except Exception:
        return 100


def max_upload_bytes() -> int:
    return max(1, max_upload_mb()) * 1024 * 1024
