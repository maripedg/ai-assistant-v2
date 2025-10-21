from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.models.feedback import FeedbackCreate, FeedbackOut
from backend.core.db.session import get_db
from backend.core.repos.factory import get_feedback_repo
from backend.common.sanitizer import sanitize_if_enabled


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


@router.post("/", response_model=FeedbackOut)
def create_feedback(payload: FeedbackCreate, db: Session = Depends(get_db)):
    repo = get_feedback_repo(session=db)
    safe_comment, _counters = sanitize_if_enabled(payload.comment or "", doc_id="feedback")
    data = payload.model_dump()
    data["comment"] = safe_comment
    fb = repo.create(data)
    logger.info("feedback.create user_id=%s category=%s", payload.user_id, payload.category)
    return fb


@router.get("/", response_model=List[FeedbackOut])
def list_feedback(
    user_id: Optional[int] = None,
    category: Optional[str] = None,
    date_from: Optional[str] = Query(None, description="ISO8601 start datetime"),
    date_to: Optional[str] = Query(None, description="ISO8601 end datetime"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    repo = get_feedback_repo(session=db)
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    items, _ = repo.list(user_id=user_id, category=category, date_from=df, date_to=dt, limit=limit, offset=offset)
    return items


@router.get("/{fb_id}", response_model=FeedbackOut)
def get_feedback(fb_id: int, db: Session = Depends(get_db)):
    repo = get_feedback_repo(session=db)
    fb = repo.get(fb_id)
    if not fb:
        raise HTTPException(status_code=404, detail="feedback_not_found")
    return fb

