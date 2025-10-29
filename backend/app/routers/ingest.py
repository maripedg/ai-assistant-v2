from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status

from backend.app.schemas.ingest import CreateIngestJobRequest, IngestJobStatus, UploadMeta
from backend.app.services.ingest import (
    ConflictError,
    EmptyUploadError,
    FileTooLargeError,
    UnknownProfileError,
    UnsupportedContentTypeError,
    ingest_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


@router.post(
    "/uploads",
    response_model=UploadMeta,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for ingestion",
)
async def upload_document(
    file: UploadFile = File(..., description="Document to upload"),
    source: Optional[str] = Form(None),
    tags: Optional[str] = Form(None, description="CSV or JSON list of tags"),
    lang_hint: Optional[str] = Form("auto"),
) -> UploadMeta:
    try:
        meta = ingest_service.save_upload(file, source, tags, lang_hint)
    except EmptyUploadError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except UnsupportedContentTypeError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Upload failed") from exc
    finally:
        await file.close()

    return meta


@router.get(
    "/uploads/{upload_id}",
    response_model=UploadMeta,
    summary="Fetch metadata for a staged upload",
)
def get_upload(upload_id: str) -> UploadMeta:
    meta = ingest_service.get_upload(upload_id)
    if not meta:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return meta


@router.post(
    "/ingest/jobs",
    response_model=IngestJobStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create an ingestion job from staged uploads",
)
def create_ingest_job(payload: CreateIngestJobRequest, background_tasks: BackgroundTasks) -> IngestJobStatus:
    try:
        job = ingest_service.create_job(payload)
    except UnknownProfileError as exc:
        logger.error("Unknown ingest profile requested: %s", exc.profile)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_400_BAD_REQUEST
        if "profile" in message.lower():
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status_code, detail=message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Conflicting job {exc}") from exc
    except KeyError as exc:
        missing = ", ".join(exc.args)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Upload not found: {missing}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to create ingest job: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to create job") from exc

    background_tasks.add_task(ingest_service.run_job, job.job_id)
    return job


@router.get(
    "/ingest/jobs/{job_id}",
    response_model=IngestJobStatus,
    summary="Retrieve the status of an ingestion job",
)
def get_ingest_job(job_id: str) -> IngestJobStatus:
    job = ingest_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
