from __future__ import annotations

"""
Alembic environment.

Ensures the repository root is on sys.path so imports like `backend.*` work
when running `alembic` from the repo root without setting PYTHONPATH.

Resolves the SQLAlchemy URL from (in order):
1) env var `DATABASE_SQLALCHEMY_URL` (or `SQLALCHEMY_URL`/`DATABASE_URL`),
2) backend/config/app.yaml -> database.sqlalchemy_url,
3) backend/config/app.yaml -> database.{user,password,host,port,service},
4) environment DB_* variables (DB_USER/DB_PASSWORD and DB_DSN or DB_HOST/DB_PORT/DB_SERVICE).
"""

import os
import sys
from pathlib import Path
from logging.config import fileConfig
from typing import Optional

from sqlalchemy import pool, create_engine
from alembic import context

# --- Path bootstrap (repo root) ---
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def _load_env_files() -> None:
    """Lightweight .env loader for Alembic context.

    Loads key=value pairs from repo-level .env files to os.environ
    without requiring python-dotenv.
    """
    candidates = [REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "=" not in s:
                        continue
                    key, val = s.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    os.environ[key] = val
        except Exception:
            pass

_load_env_files()

from backend.core.db.base import Base  # noqa: E402
from backend.core.models import users, feedback  # noqa: F401,E402 - import models for metadata


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _compose_oracle_url(user: Optional[str], password: Optional[str], host: Optional[str], port: Optional[str], service: Optional[str]) -> Optional[str]:
    if user and password and host and service:
        port = port or "1521"
        return f"oracle+oracledb://{user}:{password}@{host}:{port}/?service_name={service}"
    return None


def _parse_dsn(dsn: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    # dsn format: host:port/SERVICE
    try:
        host_part, service = dsn.split("/", 1)
        host, port = host_part.split(":", 1)
        return host, port, service
    except Exception:
        return None, None, None


def _read_app_yaml_url() -> Optional[str]:
    # Try to read backend/config/app.yaml directly
    app_yaml = REPO_ROOT / "backend" / "config" / "app.yaml"
    if not app_yaml.exists():
        return None
    try:
        import yaml  # type: ignore

        with app_yaml.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        db = (data.get("database") or {}) if isinstance(data, dict) else {}
        url = db.get("sqlalchemy_url")
        if url:
            return str(url)
        user = db.get("user") or os.getenv("DB_USER")
        password = db.get("password") or os.getenv("DB_PASSWORD")
        host = db.get("host") or os.getenv("DB_HOST")
        port = (db.get("port") or os.getenv("DB_PORT") or "1521")
        service = db.get("service") or os.getenv("DB_SERVICE")
        if not (host and service):
            dsn = os.getenv("DB_DSN")
            if dsn:
                h, p, s = _parse_dsn(dsn)
                host = host or h
                port = port or p
                service = service or s
        return _compose_oracle_url(user, password, host, port, service)
    except Exception:
        return None


def _get_sqlalchemy_url() -> str:
    # 1) Env overrides
    url = (
        os.getenv("DATABASE_SQLALCHEMY_URL")
        or os.getenv("SQLALCHEMY_URL")
        or os.getenv("DATABASE_URL")
    )
    if url:
        return url
    # 2/3) From app.yaml (direct url or composed)
    url = _read_app_yaml_url()
    if url:
        return url
    # 4) Raw env DB_* composition
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT") or "1521"
    service = os.getenv("DB_SERVICE")
    if not (host and service):
        dsn = os.getenv("DB_DSN")
        if dsn:
            h, p, s = _parse_dsn(dsn)
            host = host or h
            port = port or p
            service = service or s
    url = _compose_oracle_url(user, password, host, port, service)
    if url:
        return url
    raise RuntimeError("Database URL not configured for Alembic")


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _get_sqlalchemy_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _get_sqlalchemy_url()
    connectable = create_engine(url, poolclass=pool.NullPool, future=True)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
