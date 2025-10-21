from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.core.models.feedback import Feedback


class FeedbackRepoDB:
    def __init__(self, session: Session):
        self.session = session

    def create(self, data: Dict[str, Any]) -> Feedback:
        # Normalize payload and handle reserved/driver-incompatible types
        payload = dict(data)
        # Map 'metadata' -> 'metadata_json' and serialize non-text values
        if "metadata" in payload and "metadata_json" not in payload:
            payload["metadata_json"] = payload.pop("metadata")
        if "metadata_json" in payload and payload["metadata_json"] is not None and not isinstance(
            payload["metadata_json"], (str, bytes)
        ):
            try:
                payload["metadata_json"] = json.dumps(payload["metadata_json"], ensure_ascii=False)
            except Exception:
                payload["metadata_json"] = str(payload["metadata_json"])  # last-resort

        fb = Feedback(**payload)
        self.session.add(fb)
        self.session.flush()
        return fb

    def get(self, fb_id: int) -> Optional[Feedback]:
        return self.session.get(Feedback, fb_id)

    def list(
        self,
        *,
        user_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Feedback], int]:
        q = select(Feedback)
        if user_id is not None:
            q = q.where(Feedback.user_id == user_id)
        if category:
            q = q.where(Feedback.category == category)
        if date_from is not None:
            q = q.where(Feedback.created_at >= date_from)
        if date_to is not None:
            q = q.where(Feedback.created_at <= date_to)
        cq = select(func.count()).select_from(q.subquery())
        total = self.session.execute(cq).scalar_one()
        q = q.order_by(Feedback.created_at.desc()).limit(limit).offset(offset)
        rows = self.session.execute(q).scalars().all()
        return rows, total
