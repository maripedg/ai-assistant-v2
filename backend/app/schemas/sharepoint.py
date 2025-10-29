from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class SyncRunRequest(BaseModel):
    mode: str = Field(default="update")
    folder_name: str = Field(default="rolling")


class SyncRunResponse(BaseModel):
    sync_id: str
    mode: str
    site_key: str
    target_directory: Optional[str] = None
    uploads_registered: int
    job_id: Optional[str] = None
    status: str
    started_at: str
    finished_at: Optional[str] = None
    errors: Optional[list[Dict[str, Any]]] = None


class HistoryResponse(BaseModel):
    data: Dict[str, Any]
