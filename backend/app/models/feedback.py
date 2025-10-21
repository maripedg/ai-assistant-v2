from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class FeedbackCreate(BaseModel):
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    rating: Optional[int] = None
    category: Optional[str] = None
    comment: str
    metadata: Optional[Any] = None


class FeedbackOut(BaseModel):
    id: int
    user_id: Optional[int]
    session_id: Optional[str]
    rating: Optional[int]
    category: Optional[str]
    comment: Optional[str]
    metadata: Optional[Any] = Field(alias="metadata_json")
    created_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
