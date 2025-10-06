import os
import json
import hashlib
import csv
from pathlib import Path
from typing import Dict, List


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

