from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app import config as app_config
from backend.app.schemas.ingest import CreateIngestJobRequest
from backend.app.services import ingest as ingest_service_module
from backend.app.services.sharepoint_sync import SharePointSyncError, sharepoint_client
from backend.app.services.sync_registry import SyncRegistry, sync_registry
from backend.app.services.storage import detect_content_type_for_path

logger = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _max_upload_bytes() -> int:
    return app_config.max_upload_bytes()


def _allowed_tokens() -> set[str]:
    return {item.lower().lstrip(".") for item in app_config.allow_mime()}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_sharepoint_sync(
    mode: str = "update",
    folder_name: str = "rolling",
    registry: SyncRegistry = sync_registry,
) -> Dict[str, Any]:
    download_dir = Path(app_config.sp_download_dir()).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)

    site_key = app_config.sp_site_key()
    sync_record = registry.start_sync(mode)
    uploads_registered = 0
    upload_ids: List[str] = []
    errors: List[Dict[str, Any]] = []
    job_id: Optional[str] = None
    status = "running"
    finished_at: Optional[str] = None
    target_directory: Optional[str] = None

    ingest_service = ingest_service_module.ingest_service

    try:
        if mode == "download":
            response = sharepoint_client.download(site_key=site_key, folder=folder_name)
        else:
            response = sharepoint_client.update(site_key=site_key, folder=folder_name)

        if not isinstance(response, dict):
            raise SharePointSyncError("SharePoint sync service returned invalid payload")
        if response.get("status") != "success":
            raise SharePointSyncError(f"SharePoint sync service returned status={response.get('status')!r}")

        summary = response.get("download_summary") or {}
        target_directory = summary.get("target_directory")
        if not target_directory:
            raise SharePointSyncError("SharePoint response missing download_summary.target_directory")

        files_details = response.get("downloaded_files_details") or []
        allowed_tokens = _allowed_tokens()
        max_bytes = _max_upload_bytes()

        for entry in files_details:
            if not isinstance(entry, dict):
                continue
            if entry.get("download_success") is not True:
                continue

            raw_path = entry.get("relative_path") or entry.get("download_path")
            if not raw_path:
                errors.append({"file": entry.get("name"), "error": "missing_path"})
                continue

            file_path = Path(raw_path)
            if not file_path.is_absolute():
                file_path = (download_dir / raw_path).resolve()
            else:
                file_path = file_path.resolve()

            try:
                file_path.relative_to(download_dir)
            except ValueError:
                logger.warning("Skipping file outside SP_DOWNLOAD_DIR: %s", file_path)
                errors.append({"file": str(raw_path), "error": "outside_base_dir"})
                continue

            if not file_path.exists():
                logger.warning("Skipping missing file referenced by SharePoint sync: %s", file_path)
                errors.append({"file": str(file_path), "error": "file_missing"})
                continue

            ext = (entry.get("extension") or file_path.suffix or "").lower().lstrip(".")
            if allowed_tokens and ext not in allowed_tokens:
                logger.info("Skipping disallowed extension %s (%s)", ext or "<unknown>", file_path)
                continue

            size_bytes = file_path.stat().st_size
            expected_size = entry.get("size_bytes")
            if isinstance(expected_size, int) and expected_size != size_bytes:
                logger.warning(
                    "Size mismatch for %s (expected=%s actual=%s)", file_path, expected_size, size_bytes
                )
            if size_bytes > max_bytes:
                logger.warning("Skipping file exceeding size limit (%s bytes): %s", size_bytes, file_path)
                continue

            sha256 = _hash_file(file_path)
            if registry.exists_sha256(sha256):
                logger.debug("Skipping already ingested file: %s", file_path)
                continue

            try:
                content_type = detect_content_type_for_path(file_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unable to detect content type for %s: %s", file_path, exc)
                errors.append({"file": str(file_path), "error": "content_type_detection_failed"})
                continue

            mime_token = (content_type or "").lower()
            simple_token = mime_token.split("/")[-1]
            if allowed_tokens and (
                ext not in allowed_tokens
                and mime_token not in allowed_tokens
                and simple_token not in allowed_tokens
            ):
                logger.debug("Skipping file %s due to content type %s", file_path, content_type)
                continue

            storage_token = str(file_path)
            metadata: Dict[str, Any] = {}
            if entry.get("id"):
                metadata["sp_item_id"] = entry["id"]
            if entry.get("etag"):
                metadata["sp_etag"] = entry["etag"]
            if entry.get("lastModified"):
                metadata["sp_last_modified"] = entry["lastModified"]
            if entry.get("sharepoint_url"):
                metadata["sp_url"] = entry["sharepoint_url"]
            metadata["sp_ext"] = ext
            metadata["sp_target_dir"] = target_directory

            tags = [
                "source:sharepoint",
                "integration:sp-sync",
                f"site:{site_key}",
                f"dir:{target_directory}",
            ]

            registry.register_upload(
                storage_path=storage_token,
                size_bytes=size_bytes,
                content_type=content_type,
                sha256=sha256,
                metadata=metadata,
                tags=tags,
            )

            upload_meta = ingest_service.register_external_upload(
                abs_path=str(file_path),
                storage_path=storage_token,
                size_bytes=size_bytes,
                content_type=content_type,
                checksum_sha256=sha256,
                source="sharepoint-sync",
                tags=tags,
                metadata=metadata,
            )
            uploads_registered += 1
            upload_ids.append(upload_meta.upload_id)
            logger.info("Registered SharePoint file %s as upload %s", file_path, upload_meta.upload_id)

        if upload_ids:
            job_tags = [
                "source:sharepoint",
                "integration:sp-sync",
                f"site:{site_key}",
                f"dir:{target_directory}",
            ]
            request = CreateIngestJobRequest(
                upload_ids=upload_ids,
                profile=app_config.embed_profile(),
                tags=job_tags,
                update_alias=app_config.embed_update_alias(),
                evaluate=app_config.embed_evaluate(),
            )
            job_status = ingest_service.create_job(request)
            job_id = job_status.job_id
            ingest_service.run_job(job_id)
            status = "succeeded"
        else:
            status = "succeeded"

    except SharePointSyncError as exc:
        status = "failed"
        errors.append({"error": "sharepoint_sync_failed", "detail": str(exc)})
        logger.exception("SharePoint sync failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        errors.append({"error": "unexpected_error", "detail": str(exc)})
        logger.exception("Unexpected error during SharePoint sync: %s", exc)
    finally:
        finished_at = _utc_iso()
        registry.finish_sync(
            sync_record.sync_id,
            status=status,
            uploads_registered=uploads_registered,
            job_id=job_id,
            errors=errors if errors else None,
            finished_at=finished_at,
        )

    return {
        "sync_id": sync_record.sync_id,
        "mode": mode,
        "site_key": site_key,
        "target_directory": target_directory,
        "uploads_registered": uploads_registered,
        "job_id": job_id,
        "status": status,
        "started_at": sync_record.started_at,
        "finished_at": finished_at,
        "errors": errors or None,
    }


__all__ = ["run_sharepoint_sync"]
