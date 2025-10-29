from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from backend.app import config as app_config
from backend.app.schemas.ingest import CreateIngestJobRequest, IngestJobStatus, UploadMeta
from backend.app.services.embed_runner import run_embed_job_via_cli
from backend.app.services.storage import (
    EmptyUploadError,
    FileTooLargeError,
    StorageError,
    StorageService,
    UnsupportedContentTypeError,
)
from backend.app.deps import settings as app_settings

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 40


class ConflictError(Exception):
    """Raised when a conflicting active job already exists."""


class UnknownProfileError(Exception):
    """Raised when an ingest profile is not recognised."""

    def __init__(self, profile: Optional[str]) -> None:
        self.profile = profile or ""
        super().__init__(f"Unknown profile: {self.profile}")


def _utc_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class IngestService:
    def __init__(
        self,
        settings: Optional[Any] = None,
        staging_dir: Optional[str] = None,
        allow_mime: Optional[List[str]] = None,
        max_upload_bytes: Optional[int] = None,
    ) -> None:
        self.settings = settings
        self._storage = StorageService(staging_dir, allow_mime, max_upload_bytes)
        self._uploads_path = self._storage.base_dir / "uploads.json"
        self._jobs_path = self._storage.base_dir / "jobs.json"
        _ensure_parent(self._uploads_path)
        _ensure_parent(self._jobs_path)
        self._lock = threading.RLock()
        self._log_cache: Dict[str, Deque[str]] = {}

    # ---------- Uploads ----------
    def save_upload(
        self,
        file,
        source: Optional[str],
        tags_value: Optional[str],
        lang_hint: Optional[str],
    ) -> UploadMeta:
        stored = self._storage.save_upload(file, source, tags_value, lang_hint)
        upload_record = {
            "upload_id": stored.upload_id,
            "filename": stored.filename,
            "size_bytes": stored.size_bytes,
            "content_type": stored.content_type,
            "source": stored.source,
            "tags": stored.tags,
            "lang_hint": stored.lang_hint,
            "storage_path": stored.storage_path,
            "abs_path": stored.abs_path,
            "checksum_sha256": stored.checksum_sha256,
            "created_at": stored.created_at,
        }
        with self._lock:
            uploads = self._read_json(self._uploads_path)
            uploads[stored.upload_id] = upload_record
            self._write_json(self._uploads_path, uploads)
        return UploadMeta(**self._public_upload(upload_record))

    def register_external_upload(
        self,
        abs_path: str,
        storage_path: str,
        size_bytes: int,
        content_type: str,
        checksum_sha256: str,
        source: str = "sharepoint-sync",
        lang_hint: str = "auto",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> UploadMeta:
        upload_id = str(uuid.uuid4())
        record = {
            "upload_id": upload_id,
            "filename": Path(storage_path).name,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "source": source,
            "tags": tags or ["sharepoint"],
            "lang_hint": lang_hint,
            "storage_path": storage_path,
            "abs_path": str(Path(abs_path).resolve()),
            "checksum_sha256": checksum_sha256,
            "created_at": _utc_iso(),
            "metadata": metadata or {},
        }
        with self._lock:
            uploads = self._read_json(self._uploads_path)
            uploads[upload_id] = record
            self._write_json(self._uploads_path, uploads)
        return UploadMeta(**self._public_upload(record))

    def get_upload(self, upload_id: str) -> Optional[UploadMeta]:
        with self._lock:
            uploads = self._read_json(self._uploads_path)
            record = uploads.get(upload_id)
        if not record:
            return None
        return UploadMeta(**self._public_upload(record))

    def _public_upload(self, record: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(record)
        data.pop("abs_path", None)
        return data

    # ---------- Jobs ----------
    def create_job(self, payload: CreateIngestJobRequest) -> IngestJobStatus:
        upload_ids = payload.upload_ids
        if len(set(upload_ids)) != len(upload_ids):
            raise ValueError("upload_ids must be unique")
        with self._lock:
            uploads = self._read_json(self._uploads_path)
            missing = [uid for uid in upload_ids if uid not in uploads]
            if missing:
                raise KeyError(",".join(missing))

            jobs = self._read_json(self._jobs_path)
            if self._has_conflict(jobs, upload_ids):
                raise ConflictError("active job already references one of the uploads")

            profile = self._resolve_profile(payload.profile)
            job_id = self._new_job_id()
            job_data = {
                "job_id": job_id,
                "status": "queued",
                "profile": profile,
                "upload_ids": upload_ids,
                "created_at": _utc_iso(),
                "started_at": None,
                "finished_at": None,
                "current_phase": None,
                "inputs": {
                    "uploads_count": len(upload_ids),
                    "tags": payload.tags or [],
                    "lang_hint": payload.lang_hint or "auto",
                    "priority": payload.priority,
                    "update_alias": payload.update_alias,
                    "evaluate": payload.evaluate,
                },
                "progress": {
                    "files_total": len(upload_ids),
                    "files_processed": 0,
                    "chunks_total": 0,
                    "chunks_indexed": 0,
                    "dedupe_skipped": 0,
                },
                "summary": None,
                "metrics": None,
                "error": None,
                "logs_tail": [],
            }
            jobs[job_id] = job_data
            self._write_json(self._jobs_path, jobs)
            self._log_cache[job_id] = deque(maxlen=MAX_LOG_LINES)

        return self._status_from_dict(job_data)

    def get_job(self, job_id: str) -> Optional[IngestJobStatus]:
        with self._lock:
            jobs = self._read_json(self._jobs_path)
            job = jobs.get(job_id)
        if not job:
            return None
        return self._status_from_dict(job)

    # ---------- Runtime ----------
    def run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            logger.warning("Job %s not found", job_id)
            return

        with self._lock:
            jobs = self._read_json(self._jobs_path)
            record = jobs.get(job_id)
            if not record:
                return
            record["status"] = "running"
            record["started_at"] = _utc_iso()
            record["current_phase"] = "embedding"
            record["progress"]["files_processed"] = 0
            self._write_json(self._jobs_path, jobs)

        uploads = self._load_upload_records(record["upload_ids"])
        if not uploads:
            self._fail_job(job_id, "validation", "No uploads available for job", retryable=False)
            return

        manifest_path = self._build_manifest(job_id, record, uploads)
        start = time.time()
        inputs = record["inputs"]
        exit_code = run_embed_job_via_cli(
            manifest_path,
            record["profile"],
            bool(inputs.get("update_alias")),
            bool(inputs.get("evaluate")),
            log_callback=lambda line: self._append_log(job_id, line),
        )
        duration = max(time.time() - start, 0.0)

        log_tail = self._log_cache.get(job_id, deque())
        summary = self._derive_summary(log_tail, len(uploads), bool(inputs.get("update_alias")))
        metrics = self._derive_metrics(duration, summary, bool(inputs.get("evaluate")))

        if exit_code == 0:
            with self._lock:
                jobs = self._read_json(self._jobs_path)
                rec = jobs.get(job_id)
                if rec:
                    rec["status"] = "succeeded"
                    rec["finished_at"] = _utc_iso()
                    rec["current_phase"] = None
                    rec["summary"] = summary
                    rec["metrics"] = metrics
                    rec["progress"]["files_processed"] = len(uploads)
                    rec["progress"]["chunks_total"] = summary.get("chunks_total", 0)
                    rec["progress"]["chunks_indexed"] = summary.get("chunks_indexed", 0)
                    rec["progress"]["dedupe_skipped"] = summary.get("dedupe_skipped", 0)
                    rec["logs_tail"] = list(log_tail)
                    self._write_json(self._jobs_path, jobs)
        else:
            self._fail_job(
                job_id,
                "embedding",
                f"Embed CLI exited with code {exit_code}",
                retryable=True,
                metrics=metrics,
                logs=list(log_tail),
            )

    def _fail_job(
        self,
        job_id: str,
        phase: str,
        message: str,
        retryable: bool,
        metrics: Optional[Dict[str, Any]] = None,
        logs: Optional[List[str]] = None,
    ) -> None:
        with self._lock:
            jobs = self._read_json(self._jobs_path)
            rec = jobs.get(job_id)
            if not rec:
                return
            rec["status"] = "failed"
            rec["finished_at"] = _utc_iso()
            rec["current_phase"] = None
            rec["error"] = {"phase": phase, "message": message, "retryable": bool(retryable)}
            if metrics is not None:
                rec["metrics"] = metrics
            if logs is not None:
                rec["logs_tail"] = logs[-MAX_LOG_LINES:]
            self._write_json(self._jobs_path, jobs)

    def _append_log(self, job_id: str, line: str) -> None:
        cache = self._log_cache.setdefault(job_id, deque(maxlen=MAX_LOG_LINES))
        cache.append(line)
        with self._lock:
            jobs = self._read_json(self._jobs_path)
            rec = jobs.get(job_id)
            if rec:
                rec["logs_tail"] = list(cache)
                self._write_json(self._jobs_path, jobs)

    # ---------- Helpers ----------
    def _read_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read JSON file %s: %s", path, exc)
        return {}

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _new_job_id(self) -> str:
        return f"emb-{dt.datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

    def _resolve_profile(self, profile: Optional[str]) -> str:
        app_cfg = self._settings_app()
        embeddings_cfg = app_cfg.get("embeddings", {}) if isinstance(app_cfg, dict) else {}
        configured_profiles = self._configured_profiles(app_cfg, embeddings_cfg)
        builtin_profiles = self._builtin_profiles()
        builtin_set = set(builtin_profiles)

        if profile:
            if profile in configured_profiles:
                return profile
            if profile in builtin_set:
                return profile
            raise UnknownProfileError(profile)

        active = None
        if isinstance(embeddings_cfg, dict):
            active = embeddings_cfg.get("active_profile")

        if active and (active in configured_profiles or active in builtin_set):
            return active

        if configured_profiles:
            return next(iter(configured_profiles))

        if builtin_profiles:
            return builtin_profiles[0]

        raise ValueError("No active embedding profile configured")

    def _settings_app(self) -> Dict[str, Any]:
        if not self.settings:
            return {}
        app_section = getattr(self.settings, "app", {})
        return app_section if isinstance(app_section, dict) else {}

    @staticmethod
    def _configured_profiles(app_cfg: Dict[str, Any], embeddings_cfg: Dict[str, Any]) -> List[str]:
        ingest_profiles = app_cfg.get("ingest_profiles", {})
        if isinstance(ingest_profiles, dict) and ingest_profiles:
            return list(ingest_profiles.keys())
        if isinstance(embeddings_cfg, dict):
            embed_profiles = embeddings_cfg.get("profiles", {})
            if isinstance(embed_profiles, dict):
                return list(embed_profiles.keys())
        return []

    @staticmethod
    def _builtin_profiles() -> List[str]:
        defaults: List[str] = []
        env_profile = app_config.embed_profile()
        if env_profile and env_profile not in defaults:
            defaults.append(env_profile)
        for candidate in ("legacy_profile", "standard_profile"):
            if candidate not in defaults:
                defaults.append(candidate)
        return defaults

    def _has_conflict(self, jobs: Dict[str, Any], upload_ids: List[str]) -> bool:
        active_states = {"queued", "running"}
        for job in jobs.values():
            if job.get("status") in active_states:
                job_uploads = set(job.get("upload_ids") or [])
                if job_uploads.intersection(upload_ids):
                    return True
        return False

    def _load_upload_records(self, upload_ids: List[str]) -> List[Dict[str, Any]]:
        with self._lock:
            uploads = self._read_json(self._uploads_path)
        records = []
        for uid in upload_ids:
            record = uploads.get(uid)
            if record:
                records.append(record)
        return records

    def _build_manifest(self, job_id: str, job_record: Dict[str, Any], uploads: List[Dict[str, Any]]) -> Path:
        manifest_dir = self._storage.base_dir / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{job_id}.jsonl"

        inputs = job_record.get("inputs") or {}
        job_tags = inputs.get("tags") or []
        job_priority = inputs.get("priority")
        job_lang = (inputs.get("lang_hint") or "auto").lower()

        with manifest_path.open("w", encoding="utf-8") as handle:
            for item in uploads:
                entry = {
                    "path": item["abs_path"],
                    "doc_id": item["upload_id"],
                }
                tags = sorted({*(item.get("tags") or []), *job_tags})
                if tags:
                    entry["tags"] = tags
                lang = job_lang
                upload_lang = (item.get("lang_hint") or "auto").lower()
                if lang == "auto" and upload_lang != "auto":
                    lang = upload_lang
                if lang != "auto":
                    entry["lang"] = lang
                if job_priority is not None:
                    entry["priority"] = job_priority
                entry["metadata"] = {
                    "source": item.get("source"),
                    "content_type": item.get("content_type"),
                    "checksum_sha256": item.get("checksum_sha256"),
                }
                handle.write(json.dumps(entry, ensure_ascii=False))
                handle.write("\n")

        return manifest_path

    def _derive_summary(self, log_tail: Deque[str], total_files: int, updated_alias: bool) -> Dict[str, Any]:
        summary = {
            "files_total": total_files,
            "files_processed": total_files,
            "chunks_indexed": 0,
            "dedupe_skipped": 0,
            "chunks_total": 0,
            "updated_alias": updated_alias,
        }
        for line in reversed(log_tail):
            if "docs=" in line and "chunks=" in line and "inserted=" in line:
                try:
                    parts = line.split()
                    for part in parts:
                        if part.startswith("chunks="):
                            summary["chunks_total"] = int(part.split("=")[1])
                        elif part.startswith("inserted="):
                            summary["chunks_indexed"] = int(part.split("=")[1])
                        elif part.startswith("skipped="):
                            summary["dedupe_skipped"] = int(part.split("=")[1])
                except Exception:
                    pass
                break
        return summary

    def _derive_metrics(self, duration: float, summary: Dict[str, Any], evaluate: bool) -> Dict[str, Any]:
        chunks = summary.get("chunks_total") or summary.get("chunks_indexed") or 0
        throughput = (chunks / duration) if duration > 0 else 0.0
        return {
            "duration_sec": round(duration, 3),
            "throughput_chunks_per_s": round(throughput, 3) if throughput else 0.0,
            "evaluate": bool(evaluate),
        }

    def _status_from_dict(self, record: Dict[str, Any]) -> IngestJobStatus:
        payload = {
            "job_id": record["job_id"],
            "status": record["status"],
            "profile": record["profile"],
            "created_at": record["created_at"],
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "current_phase": record.get("current_phase"),
            "inputs": record.get("inputs") or {},
            "progress": record.get("progress"),
            "summary": record.get("summary"),
            "metrics": record.get("metrics"),
            "logs_tail": list(record.get("logs_tail") or []),
            "error": record.get("error"),
        }
        return IngestJobStatus(**payload)


def build_ingest_service(settings_obj: Optional[Any] = None) -> IngestService:
    settings_obj = settings_obj or app_settings
    return IngestService(
        settings=settings_obj,
        staging_dir=app_config.staging_dir(),
        allow_mime=list(app_config.allow_mime()),
        max_upload_bytes=app_config.max_upload_bytes(),
    )


ingest_service = build_ingest_service()


__all__ = [
    "ingest_service",
    "StorageError",
    "EmptyUploadError",
    "FileTooLargeError",
    "UnsupportedContentTypeError",
    "ConflictError",
    "IngestService",
    "UnknownProfileError",
]
