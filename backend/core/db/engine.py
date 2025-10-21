from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional, Tuple

from sqlalchemy import create_engine, text

try:
    # Import settings lazily to avoid heavy deps at module import
    from backend.app.deps import settings  # type: ignore
except Exception:  # pragma: no cover - during alembic offline
    settings = None  # type: ignore

logger = logging.getLogger(__name__)


def _compose_from_parts(
    user: Optional[str], password: Optional[str], host: Optional[str], port: Optional[str], service: Optional[str]
) -> Optional[str]:
    if user and password and host and service:
        port = port or "1521"
        return f"oracle+oracledb://{user}:{password}@{host}:{port}/?service_name={service}"
    return None


def _build_sqlalchemy_url_from_env() -> Optional[str]:
    # Prefer explicit URL from env
    url = (
        os.getenv("DATABASE_SQLALCHEMY_URL")
        or os.getenv("SQLALCHEMY_URL")
        or os.getenv("DATABASE_URL")
    )
    if url:
        return url
    # Compose from DB_* envs. Prefer DB_DSN if present (host:port/SERVICE)
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    dsn = os.getenv("DB_DSN")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT") or "1521"
    service = os.getenv("DB_SERVICE")

    if dsn and (not host or not service):
        try:
            host_part, svc_part = dsn.split("/", 1)
            host_s, port_s = host_part.split(":", 1)
            host = host or host_s
            port = port or port_s
            service = service or svc_part
        except Exception:
            logger.debug("Unable to parse DB_DSN=%r", dsn)

    return _compose_from_parts(user, password, host, port, service)


def resolve_db_url() -> Tuple[str, str]:
    """Resolve DB URL and its source label: env|config|composed.

    Precedence: env(DATABASE_SQLALCHEMY_URL|SQLALCHEMY_URL|DATABASE_URL),
    then config.database.sqlalchemy_url, then compose from parts using
    config.database fields or DB_* envs.
    """
    # 1) env direct
    url = (
        os.getenv("DATABASE_SQLALCHEMY_URL")
        or os.getenv("SQLALCHEMY_URL")
        or os.getenv("DATABASE_URL")
    )
    if url:
        return url, "env"

    # 2) app config value
    app_cfg = getattr(settings, "app", {}) if settings is not None else {}
    db_cfg = (app_cfg or {}).get("database", {}) if isinstance(app_cfg, dict) else {}
    url = (db_cfg or {}).get("sqlalchemy_url") or None
    if url:
        return url, "config"

    # 3) compose from parts (config or env)
    user = db_cfg.get("user") or os.getenv("DB_USER")
    password = db_cfg.get("password") or os.getenv("DB_PASSWORD")
    host = db_cfg.get("host") or os.getenv("DB_HOST")
    port = (db_cfg.get("port") or os.getenv("DB_PORT") or "1521")
    service = db_cfg.get("service") or os.getenv("DB_SERVICE")
    composed = _compose_from_parts(user, password, host, port, service)
    if composed:
        return composed, "composed"
    raise RuntimeError("Database URL not configured. Set DATABASE_SQLALCHEMY_URL, database.sqlalchemy_url, or DB_* envs.")


def mask_url(url: str) -> str:
    try:
        # oracle+oracledb://user:pass@host:port/?service_name=...
        if "://" not in url:
            return url
        scheme_rest = url.split("://", 1)
        rest = scheme_rest[1]
        creds_host = rest.split("@", 1)
        if len(creds_host) < 2:
            return url
        user_pass = creds_host[0]
        if ":" in user_pass:
            user = user_pass.split(":", 1)[0]
            masked = f"{user}:***"
        else:
            masked = "***"
        return f"{scheme_rest[0]}://{masked}@{creds_host[1]}"
    except Exception:
        return url


def whoami(conn) -> dict:
    r = conn.execute(text("select sys_context('USERENV','SERVICE_NAME') as svc, sys_context('USERENV','CURRENT_SCHEMA') as sch from dual"))
    row = r.first()
    return {"service_name": (row and row.svc) or None, "current_schema": (row and row.sch) or None}


@lru_cache(maxsize=1)
def get_engine():
    url, source = resolve_db_url()
    app_cfg = getattr(settings, "app", {}) if settings is not None else {}
    db_cfg = (app_cfg or {}).get("database", {}) if isinstance(app_cfg, dict) else {}
    pool_min = int((db_cfg or {}).get("pool_min", 1) or 1)
    pool_max = int((db_cfg or {}).get("pool_max", 5) or 5)
    timeout = int((db_cfg or {}).get("pool_timeout_seconds", 30) or 30)

    logger.info(
        "Creating SQLAlchemy engine: url=%s (source=%s) | pool %s-%s timeout=%ss",
        mask_url(url), source, pool_min, pool_max, timeout,
    )
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_min,
        max_overflow=max(pool_max - pool_min, 0),
        pool_timeout=timeout,
        future=True,
    )
    return engine
