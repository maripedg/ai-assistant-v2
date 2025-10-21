import json
import logging
from typing import Any, Dict, Optional, Tuple

import requests
from app_config.env import get_config

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Lightweight API error wrapper for non-2xx responses."""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _json_or_text(resp: requests.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}
    return resp.text


def _request(method: str, path: str, *, params: Optional[Dict[str, Any]] = None, json_body: Any = None, timeout: int = 10) -> Any:
    """Internal HTTP helper that prefixes BACKEND_API_BASE and maps errors.

    Raises ApiError on non-2xx responses.
    """
    cfg = get_config()
    base = cfg.get("BACKEND_API_BASE", "").rstrip("/")
    url = f"{base}{path}"
    headers = {"Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        r = requests.request(method.upper(), url, params=params or {}, json=json_body, timeout=timeout, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(0, "network_error", f"Network error calling {method} {path}", str(exc)) from exc

    if 200 <= r.status_code < 300:
        return _json_or_text(r)

    details = _json_or_text(r)
    if r.status_code == 404:
        raise ApiError(404, "not_found", "Resource not found", details)
    if r.status_code == 409:
        raise ApiError(409, "conflict", "Already exists or conflict", details)
    if r.status_code == 422:
        raise ApiError(422, "validation_error", "Validation error", details)
    raise ApiError(r.status_code, "api_error", "Unexpected API error", details)


# ------- Users endpoints -------
def users_list(email: Optional[str] = None, status: Optional[str] = None, limit: int = 20, offset: int = 0) -> Any:
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if email:
        params["email"] = email
    if status:
        params["status"] = status
    return _request("GET", "/api/v1/users/", params=params)


def users_create(payload: Dict[str, Any]) -> Any:
    return _request("POST", "/api/v1/users/", json_body=payload)


def users_get(user_id: int) -> Any:
    return _request("GET", f"/api/v1/users/{user_id}")


def users_patch(user_id: int, payload: Dict[str, Any]) -> Any:
    return _request("PATCH", f"/api/v1/users/{user_id}", json_body=payload)


def users_delete(user_id: int, hard: bool = False) -> Any:
    return _request("DELETE", f"/api/v1/users/{user_id}", params={"hard": str(hard).lower()})


def users_change_password(user_id: int, payload: Dict[str, Any]) -> Any:
    return _request("POST", f"/api/v1/users/{user_id}/password", json_body=payload)


# ------- Feedback endpoints -------
def feedback_create(payload: Dict[str, Any]) -> Any:
    return _request("POST", "/api/v1/feedback/", json_body=payload)


def feedback_list(**filters: Any) -> Any:
    return _request("GET", "/api/v1/feedback/", params=filters)


# ------- Auth endpoints -------
def auth_login(email: str, password: str) -> Any:
    """Perform backend login. Returns JSON with at least {token, user:{email,role,status}}.

    Raises ApiError(401, "unauthorized", ...) on invalid credentials.
    """
    try:
        return _request("POST", "/api/v1/auth/login", json_body={"email": email, "password": password})
    except ApiError as err:
        if getattr(err, "status", None) == 401:
            raise ApiError(401, "unauthorized", "Invalid credentials", err.details)
        if getattr(err, "status", None) == 403:
            raise ApiError(403, "forbidden", "User not allowed", err.details)
        raise

class APIClient:
    def __init__(self, base_url: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health_check(self) -> Tuple[bool, Dict[str, Any]]:
        url = f"{self.base_url}/healthz"
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            ok = bool(data.get("ok", False))
            return ok, data
        except Exception as e:
            return False, {"ok": False, "error": str(e), "services": {}}

    def chat(self, question: str) -> Dict[str, Any]:
        """Devuelve siempre un dict normalizado con:
        answer, answer2, retrieved_chunks_metadata, raw, mode, used_chunks, decision_explain
        """
        url = f"{self.base_url}/chat"
        payload = {"question": question}
        out = {
            "answer": "",
            "answer2": None,
            "retrieved_chunks_metadata": [],
            "mode": None,
            "used_chunks": [],
            "decision_explain": {},
            "raw": None,
        }
        try:
            r = requests.post(url, json=payload, timeout=self.timeout, headers={"Content-Type": "application/json"})
            r.raise_for_status()
            # Backends pueden responder JSON directo con el objeto final:
            data = r.json()
            # Algunos backends anidan respuesta serializada en data["response"]:
            raw = data.get("response", data)
            out["raw"] = raw

            if isinstance(raw, str):
                # Intentar parsear string JSON:
                try:
                    parsed = json.loads(raw)
                    out.update({
                        "answer": parsed.get("answer", "") or "",
                        "answer2": parsed.get("answer2"),
                        "retrieved_chunks_metadata": parsed.get("retrieved_chunks_metadata", []) or [],
                        "mode": parsed.get("mode"),
                        "used_chunks": parsed.get("used_chunks", []) or [],
                        "decision_explain": parsed.get("decision_explain", {}) or {},
                    })
                except json.JSONDecodeError:
                    # Texto plano
                    out["answer"] = raw
            elif isinstance(raw, dict):
                out.update({
                    "answer": raw.get("answer", "") or "",
                    "answer2": raw.get("answer2"),
                    "retrieved_chunks_metadata": raw.get("retrieved_chunks_metadata", []) or [],
                    "mode": raw.get("mode"),
                    "used_chunks": raw.get("used_chunks", []) or [],
                    "decision_explain": raw.get("decision_explain", {}) or {},
                })
            else:
                # Formato inesperado: intenta mapear campos del data base
                out["answer"] = data.get("answer", "") or ""
                out["retrieved_chunks_metadata"] = data.get("retrieved_chunks_metadata", []) or []
            return out
        except Exception as e:
            return {**out, "answer": f"❌ Error contacting backend: {e}"}
