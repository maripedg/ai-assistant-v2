from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator


class UploadMeta(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    content_type: str
    source: str
    tags: List[str] = Field(default_factory=list)
    lang_hint: str = "auto"
    storage_path: str
    checksum_sha256: str
    created_at: str


class CreateIngestJobRequest(BaseModel):
    upload_ids: List[str]
    profile: Optional[str] = None
    tags: Optional[List[str]] = None
    lang_hint: Optional[Literal["auto", "es", "en", "pt"]] = "auto"
    priority: Optional[int] = None
    update_alias: bool = False
    evaluate: bool = False

    @validator("upload_ids")
    def _validate_upload_ids(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("upload_ids must not be empty")
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("upload_ids cannot be blank")
        return cleaned

    @validator("tags", pre=True)
    def _normalize_tags(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if not value:
            return None
        return [str(item).strip() for item in value if str(item).strip()]


class JobInputs(BaseModel):
    uploads_count: int
    tags: List[str] = Field(default_factory=list)
    lang_hint: str = "auto"
    priority: Optional[int] = None
    update_alias: bool = False
    evaluate: bool = False


class JobProgress(BaseModel):
    files_total: int = 0
    files_processed: int = 0
    chunks_total: int = 0
    chunks_indexed: int = 0
    dedupe_skipped: int = 0


class JobSummary(BaseModel):
    files_total: int
    files_processed: int
    chunks_indexed: int
    dedupe_skipped: int
    updated_alias: bool


class JobMetrics(BaseModel):
    duration_sec: float
    throughput_chunks_per_s: float
    evaluate: bool


class JobError(BaseModel):
    phase: str
    message: str
    retryable: bool = False


class IngestJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    profile: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    current_phase: Optional[str] = None
    inputs: JobInputs
    progress: Optional[JobProgress] = None
    summary: Optional[JobSummary] = None
    metrics: Optional[JobMetrics] = None
    logs_tail: List[str] = Field(default_factory=list)
    error: Optional[JobError] = None

    @validator("inputs", pre=True)
    def _convert_inputs(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return JobInputs(**value)
        return value

    @validator("progress", pre=True, always=True)
    def _convert_progress(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return JobProgress(**value)
        return value

    @validator("summary", pre=True)
    def _convert_summary(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return JobSummary(**value)
        return value

    @validator("metrics", pre=True)
    def _convert_metrics(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return JobMetrics(**value)
        return value

    @validator("error", pre=True)
    def _convert_error(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            return JobError(**value)
        return value
