from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone


class UsersRepoJSON:
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
        if any(r.get("email") == data.get("email") for r in rows):
            raise ValueError("email_already_exists")
        now = datetime.now(timezone.utc).isoformat()
        rec = {
            "id": self._next_id(rows),
            "created_at": now,
            "updated_at": now,
            "status": data.get("status") or "invited",
            "role": data.get("role") or "user",
            **{k: v for k, v in data.items() if k not in {"id", "created_at", "updated_at"}},
        }
        rows.append(rec)
        self._atomic_write(rows)
        return rec

    def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        for r in self._read():
            if r.get("id") == user_id:
                return r
        return None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        for r in self._read():
            if r.get("email") == email:
                return r
        return None

    def list(
        self,
        *,
        email: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows = self._read()
        if email:
            rows = [r for r in rows if email.lower() in (r.get("email") or "").lower()]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        total = len(rows)
        return rows[offset : offset + limit], total

    def update(self, user_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rows = self._read()
        updated = None
        for r in rows:
            if r.get("id") == user_id:
                r.update({k: v for k, v in data.items() if k not in {"id", "created_at"}})
                r["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated = r
                break
        if updated is not None:
            self._atomic_write(rows)
        return updated

    def delete(self, user_id: int, *, hard: bool = False) -> bool:
        rows = self._read()
        changed = False
        if hard:
            new_rows = [r for r in rows if r.get("id") != user_id]
            changed = len(new_rows) != len(rows)
            if changed:
                self._atomic_write(new_rows)
            return changed
        else:
            for r in rows:
                if r.get("id") == user_id:
                    r["status"] = "suspended"
                    r["updated_at"] = datetime.now(timezone.utc).isoformat()
                    changed = True
                    break
            if changed:
                self._atomic_write(rows)
            return changed

    def update_password(self, user_id: int, password_hash: str, algo: str) -> None:
        rows = self._read()
        now = datetime.now(timezone.utc).isoformat()
        for r in rows:
            if r.get("id") == user_id:
                r["password_hash"] = password_hash
                r["password_algo"] = algo
                r["password_updated_at"] = now
                r["updated_at"] = now
                break
        self._atomic_write(rows)
