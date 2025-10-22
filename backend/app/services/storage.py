from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from zipfile import ZipFile

from fastapi import UploadFile

from backend.app import config as app_config

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """Base storage error."""


class EmptyUploadError(StorageError):
    """Raised when the uploaded file is empty."""


class FileTooLargeError(StorageError):
    """Raised when the uploaded file exceeds the allowed size."""


class UnsupportedContentTypeError(StorageError):
    """Raised when the uploaded file MIME type is not allowed."""


_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _clean_filename(filename: str) -> str:
    filename = filename.strip()
    if not filename:
        return "file"
    # Split extension
    stem, _, suffix = filename.rpartition(".")
    if not stem:
        sanitized = _FILENAME_RE.sub("_", filename)
        return sanitized or "file"
    stem = _FILENAME_RE.sub("_", stem) or "file"
    suffix = _FILENAME_RE.sub("", suffix)
    return f"{stem}.{suffix}" if suffix else stem


def _detect_office_subtype(path: Path) -> Optional[str]:
    try:
        with ZipFile(path) as zf:
            names = set(zf.namelist())
    except Exception:
        return None
    if "word/document.xml" in names or any(name.startswith("word/") for name in names):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "ppt/presentation.xml" in names or any(name.startswith("ppt/") for name in names):
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if "xl/workbook.xml" in names or any(name.startswith("xl/") for name in names):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return None


def _sniff_mime(path: Path, filename: str) -> str:
    with path.open("rb") as handle:
        head = handle.read(4096)
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head[:4] == b"PK\x03\x04":
        subtype = _detect_office_subtype(path)
        if subtype:
            return subtype
        # Fallback to extension for zipped formats
    lowered = head.lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "text/html"
    # Naive text detection
    if all((32 <= b <= 126) or b in (9, 10, 13) for b in head[:128]):
        return "text/plain"
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    return "application/octet-stream"


def _parse_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    # Treat as CSV
    parts = [seg.strip() for seg in raw.split(",")]
    return [seg for seg in parts if seg]


@dataclass(frozen=True)
class StoredUpload:
    upload_id: str
    filename: str
    size_bytes: int
    content_type: str
    source: str
    tags: List[str]
    lang_hint: str
    storage_path: str
    abs_path: str
    checksum_sha256: str
    created_at: str


class StorageService:
    def __init__(
        self,
        staging_dir: Optional[str] = None,
        allow_mime: Optional[Iterable[str]] = None,
        max_upload_bytes: Optional[int] = None,
    ) -> None:
        staging_dir = staging_dir or app_config.staging_dir()
        self._base_dir = Path(staging_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

        allow = allow_mime or app_config.allow_mime()
        self._allow_mime = {m.lower() for m in allow}
        self._max_upload_bytes = max_upload_bytes or app_config.max_upload_bytes()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def save_upload(
        self,
        file: UploadFile,
        source: Optional[str],
        tags_value: Optional[str],
        lang_hint: Optional[str],
    ) -> StoredUpload:
        if not file or not file.filename:
            raise EmptyUploadError("No file provided")

        sanitized_name = _clean_filename(file.filename)
        upload_id = str(uuid.uuid4())
        today = dt.datetime.utcnow()
        rel_dir = Path(today.strftime("%Y")) / today.strftime("%m") / today.strftime("%d") / upload_id
        target_dir = self._base_dir / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / sanitized_name

        size_bytes = 0
        digest = hashlib.sha256()
        file.file.seek(0)
        with target_path.open("wb") as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > self._max_upload_bytes:
                    handle.close()
                    target_path.unlink(missing_ok=True)
                    raise FileTooLargeError(f"Upload exceeds maximum size of {self._max_upload_bytes} bytes")
                handle.write(chunk)
                digest.update(chunk)
        if size_bytes == 0:
            target_path.unlink(missing_ok=True)
            raise EmptyUploadError("Uploaded file is empty")

        content_type = _sniff_mime(target_path, sanitized_name).lower()
        if content_type not in self._allow_mime:
            target_path.unlink(missing_ok=True)
            raise UnsupportedContentTypeError(f"Unsupported MIME type: {content_type}")

        # Build relative storage path for API consumers
        storage_root = self._base_dir.name or "staging"
        storage_path = (Path(storage_root) / rel_dir / sanitized_name).as_posix()
        checksum = digest.hexdigest()
        created_at = today.replace(microsecond=0).isoformat() + "Z"
        tags = _parse_tags(tags_value)
        lang_hint = (lang_hint or "auto").strip().lower() or "auto"
        if lang_hint not in {"auto", "es", "en", "pt"}:
            lang_hint = "auto"

        return StoredUpload(
            upload_id=upload_id,
            filename=sanitized_name,
            size_bytes=size_bytes,
            content_type=content_type,
            source=(source or "manual-upload").strip() or "manual-upload",
            tags=tags,
            lang_hint=lang_hint,
            storage_path=storage_path,
            abs_path=str(target_path),
            checksum_sha256=checksum,
            created_at=created_at,
        )


def parse_tags_field(raw: Optional[str]) -> List[str]:
    return _parse_tags(raw)
