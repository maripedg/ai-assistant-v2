from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone


class FeedbackRepoJSON:
    def __init__(self, path: str | Path):
        self.path = self._resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write([])

    def _resolve_path(self, p: str | Path) -> Path:
        path = Path(p)
        if not path.is_absolute():
            base = Path(__file__).resolve().parents[2]  # backend/
            path = (base / path).resolve()
        return path

    def _read(self) -> List[Dict[str, Any]]:
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                return json.load(f) or []
            except Exception:
                return []

    def _atomic_write(self, data: List[Dict[str, Any]]):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _next_id(self, rows: List[Dict[str, Any]]) -> int:
        return (max((r.get("id", 0) for r in rows), default=0) + 1) if rows else 1

    def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rows = self._read()
        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "id": self._next_id(rows),
            "created_at": now,
            **data,
        }
        rows.append(rec)
        self._atomic_write(rows)
        return rec

    def get(self, fb_id: int) -> Optional[Dict[str, Any]]:
        for r in self._read():
            if r.get("id") == fb_id:
                return r
        return None

    def list(
        self,
        *,
        user_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows = self._read()
        if user_id is not None:
            rows = [r for r in rows if r.get("user_id") == user_id]
        if category:
            rows = [r for r in rows if r.get("category") == category]
        def _parse_dt(s):
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None
        if date_from is not None:
            rows = [r for r in rows if (dt := _parse_dt(r.get("created_at", ""))) and dt >= date_from]
        if date_to is not None:
            rows = [r for r in rows if (dt := _parse_dt(r.get("created_at", ""))) and dt <= date_to]
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        total = len(rows)
        return rows[offset : offset + limit], total
