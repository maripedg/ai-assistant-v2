from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app import config as app_config


def _utc_iso() -> str:
    from datetime import datetime

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    storage_path: str
    sha256: str
    size_bytes: int
    content_type: str
    created_at: str
    metadata: Dict[str, Any]
    tags: list[str]


@dataclass(frozen=True)
class SyncRunRecord:
    sync_id: str
    mode: str
    started_at: str
    finished_at: Optional[str]
    uploads_registered: int
    job_id: Optional[str]
    status: str
    errors: Optional[list[Dict[str, Any]]]


class SyncRegistry:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        download_dir = Path(app_config.sp_download_dir()).expanduser().resolve()
        download_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path or (download_dir / "sync_registry.db")
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS uploads_registry (
                    upload_id TEXT PRIMARY KEY,
                    storage_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT,
                    tags TEXT
                );
                CREATE TABLE IF NOT EXISTS sync_runs (
                    sync_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    uploads_registered INTEGER NOT NULL DEFAULT 0,
                    job_id TEXT,
                    status TEXT NOT NULL,
                    errors TEXT
                );
                """,
            )

    # ---------- Upload registry ----------
    def exists_sha256(self, sha256: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT 1 FROM uploads_registry WHERE sha256 = ? LIMIT 1", (sha256,))
            return cur.fetchone() is not None

    def get_by_path(self, storage_path: str) -> Optional[UploadRecord]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM uploads_registry WHERE storage_path = ? LIMIT 1",
                (storage_path,),
            )
            row = cur.fetchone()
        if not row:
            return None
        metadata_raw = row["metadata"]
        tags_raw = row["tags"]
        try:
            metadata = json.loads(metadata_raw) if metadata_raw else {}
        except json.JSONDecodeError:
            metadata = {}
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except json.JSONDecodeError:
            tags = []
        return UploadRecord(
            upload_id=row["upload_id"],
            storage_path=row["storage_path"],
            sha256=row["sha256"],
            size_bytes=int(row["size_bytes"]),
            content_type=row["content_type"],
            created_at=row["created_at"],
            metadata=metadata,
            tags=tags,
        )

    def register_upload(
        self,
        storage_path: str,
        size_bytes: int,
        content_type: str,
        sha256: str,
        created_at: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[list[str]] = None,
    ) -> UploadRecord:
        record = UploadRecord(
            upload_id=str(uuid.uuid4()),
            storage_path=storage_path,
            sha256=sha256,
            size_bytes=size_bytes,
            content_type=content_type,
            created_at=created_at or _utc_iso(),
            metadata=metadata or {},
            tags=tags or [],
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO uploads_registry (upload_id, storage_path, sha256, size_bytes, content_type, created_at, metadata, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.upload_id,
                    record.storage_path,
                    record.sha256,
                    record.size_bytes,
                    record.content_type,
                    record.created_at,
                    json.dumps(record.metadata),
                    json.dumps(record.tags),
                ),
            )
        return record

    # ---------- Sync runs ----------
    def start_sync(self, mode: str) -> SyncRunRecord:
        sync_id = str(uuid.uuid4())
        started_at = _utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_runs (sync_id, mode, started_at, status, uploads_registered)
                VALUES (?, ?, ?, ?, 0)
                """,
                (sync_id, mode, started_at, "running"),
            )
        return SyncRunRecord(
            sync_id=sync_id,
            mode=mode,
            started_at=started_at,
            finished_at=None,
            uploads_registered=0,
            job_id=None,
            status="running",
            errors=None,
        )

    def finish_sync(
        self,
        sync_id: str,
        status: str,
        uploads_registered: int,
        job_id: Optional[str],
        errors: Optional[list[Dict[str, Any]]] = None,
        finished_at: Optional[str] = None,
    ) -> None:
        finished_at = finished_at or _utc_iso()
        payload = json.dumps(errors or [])
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                   SET status = ?,
                       finished_at = ?,
                       uploads_registered = ?,
                       job_id = ?,
                       errors = ?
                 WHERE sync_id = ?
                """,
                (status, finished_at, uploads_registered, job_id, payload, sync_id),
            )

    def latest_syncs(self, limit: int = 20) -> list[SyncRunRecord]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        records: list[SyncRunRecord] = []
        for row in rows:
            errors_raw = row["errors"]
            try:
                errors = json.loads(errors_raw) if errors_raw else None
            except json.JSONDecodeError:
                errors = None
            records.append(
                SyncRunRecord(
                    sync_id=row["sync_id"],
                    mode=row["mode"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    uploads_registered=int(row["uploads_registered"]),
                    job_id=row["job_id"],
                    status=row["status"],
                    errors=errors,
                )
            )
        return records


sync_registry = SyncRegistry()

__all__ = ["sync_registry", "SyncRegistry", "UploadRecord", "SyncRunRecord"]
