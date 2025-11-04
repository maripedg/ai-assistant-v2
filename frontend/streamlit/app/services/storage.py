import os
import json
import hashlib
import csv
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ensure_dir(path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


# ---------- Usuarios ----------

def users_path(base_dir: str) -> str:
    _ensure_dir(base_dir)
    return str(Path(base_dir) / "usuarios.json")


def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def load_users(base_dir: str) -> Dict[str, str]:
    path = Path(users_path(base_dir))
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def save_users(base_dir: str, users: Dict[str, str]) -> None:
    path = Path(users_path(base_dir))
    _ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(users, handle, indent=2, ensure_ascii=False)


def ensure_admin(base_dir: str) -> None:
    users = load_users(base_dir)
    if "admin" not in users:
        users["admin"] = hash_password("admin")
        save_users(base_dir, users)


# ---------- Feedback ----------

def feedback_files(base_dir: str) -> Dict[str, str]:
    _ensure_dir(base_dir)
    base = Path(base_dir)
    return {
        "json": str(base / "fback.json"),
        "icon_json": str(base / "fback_icon.json"),
        "csv": str(base / "fback.csv"),
    }


def _load_json_list(path: Path) -> List[Dict]:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _write_json_list(path: Path, items: List[Dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2, ensure_ascii=False)


def append_feedback(base_dir: str, record: Dict) -> None:
    paths = feedback_files(base_dir)
    json_path = Path(paths["json"])
    data = _load_json_list(json_path)
    data.append(record)
    _write_json_list(json_path, data)

    csv_path = Path(paths["csv"])
    is_new_file = not csv_path.exists()
    _ensure_dir(csv_path.parent)
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        fieldnames = ["username", "feedback", "ts"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if is_new_file:
            writer.writeheader()
        writer.writerow(record)


def append_icon_feedback(base_dir: str, record: Dict) -> None:
    paths = feedback_files(base_dir)
    icon_path = Path(paths["icon_json"])
    data = _load_json_list(icon_path)
    data.append(record)
    _write_json_list(icon_path, data)


# ---------------- Mode-aware facades (auth & feedback) ----------------
# Lazy-config to avoid import loops
def _cfg() -> Dict[str, Any]:
    from app_config.env import get_config  # local import

    return get_config()


def _is(mode_key: str, expected: str) -> bool:
    return str(_cfg().get(mode_key, "local")).lower() == expected


def is_auth_local() -> bool:
    return _is("AUTH_MODE", "local")


def is_auth_db() -> bool:
    return _is("AUTH_MODE", "db")


def is_feedback_local() -> bool:
    return _is("FEEDBACK_MODE", "local")


def is_feedback_db() -> bool:
    return _is("FEEDBACK_MODE", "db")


def is_dual_write() -> bool:
    return bool(_cfg().get("DUAL_WRITE_FEEDBACK", False))


class StorageError(Exception):
    pass


def auth_find_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    if is_auth_local():
        users = load_users(_cfg()["AUTH_STORAGE_DIR"])
        if email in users:
            return {"email": email}
        return None
    # db mode
    from app.services import api_client as _api

    try:
        result = _api.users_list(email=email, limit=1, offset=0)
        if isinstance(result, list) and result:
            return result[0]
        return None
    except Exception:
        return None


def auth_create_user(user: Dict[str, Any]) -> Dict[str, Any]:
    if is_auth_local():
        email = user.get("email")
        if not email:
            raise StorageError("email is required")
        users = load_users(_cfg()["AUTH_STORAGE_DIR"])
        if email in users:
            raise StorageError("email already exists")
        pw = user.get("password") or ""
        users[email] = hash_password(pw) if pw else users.get(email, "")
        save_users(_cfg()["AUTH_STORAGE_DIR"], users)
        return {
            "id": -1,
            "email": email,
            "name": user.get("name"),
            "role": user.get("role", "user"),
            "status": user.get("status", "active"),
        }
    from app.services import api_client as _api

    return _api.users_create(user)


def auth_patch_user(user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if is_auth_local():
        return {"ok": True, "id": user_id, "updated": list(payload.keys())}
    from app.services import api_client as _api

    return _api.users_patch(user_id, payload)


def auth_delete_user(user_id: int, *, hard: bool = False) -> Dict[str, Any]:
    if is_auth_local():
        return {"ok": True}
    from app.services import api_client as _api

    return _api.users_delete(user_id, hard=hard)


def auth_change_password(user_id: int, new_password: str) -> Dict[str, Any]:
    if is_auth_local():
        raise StorageError("Local password change by id is not supported in this UI mode")
    from app.services import api_client as _api

    return _api.users_change_password(user_id, {"new_password": new_password})


def feedback_thumb(
    username: str,
    question: str,
    answer: str,
    is_like: bool,
    comment: Optional[str] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    icon = "like" if is_like else "dislike"
    ts_val = ts or (datetime.utcnow().isoformat(timespec="seconds") + "Z")
    local_record = {
        "username": username,
        "question": question,
        "answer": answer,
        "icon": icon,
        "feedback": comment or "",
        "ts": ts_val,
    }
    db_payload = {
        "category": icon,
        "rating": 5 if is_like else 1,
        "comment": comment or "",
        "metadata": {
            "username": username,
            "question": question,
            "answer": answer,
            "ts": ts_val,
        },
    }

    results: Dict[str, Any] = {}
    warnings: List[str] = []

    if is_dual_write():
        with suppress(Exception):
            append_icon_feedback(_cfg()["FEEDBACK_STORAGE_DIR"], local_record)
            results["local"] = {"ok": True}
        try:
            results["db"] = api_client.feedback_create(db_payload)
        except Exception as exc:  # noqa: BLE001
            results["db"] = {"ok": False, "error": str(exc)}
            warnings.append("DB write failed")
        if "local" not in results:
            warnings.append("Local write failed")
        if warnings:
            results["warning"] = "; ".join(warnings)
        return results

    if is_feedback_local():
        try:
            append_icon_feedback(_cfg()["FEEDBACK_STORAGE_DIR"], local_record)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Failed to write local feedback: {exc}") from exc

    return api_client.feedback_create(db_payload)


def feedback_list(**filters: Any) -> Dict[str, Any]:
    if is_feedback_local():
        try:
            paths = feedback_files(_cfg()["FEEDBACK_STORAGE_DIR"])
            items = _load_json_list(Path(paths["icon_json"]))
            out: List[Dict[str, Any]] = []
            for it in items:
                ok = True
                if "username" in filters and it.get("username") != filters.get("username"):
                    ok = False
                cat = filters.get("category")
                if cat and it.get("icon") != cat:
                    ok = False
                if ok:
                    out.append(it)
            return {"items": out, "count": len(out)}
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"Failed to read local feedback: {exc}") from exc
    return {"items": api_client.feedback_list(**filters)}

