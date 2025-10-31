import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def _backend_base_url() -> str:
    """Resolve backend base URL preferring FRONTEND_BASE_URL over BACKEND_API_BASE."""
    cfg = get_config()
    base = cfg.get("FRONTEND_BASE_URL") or cfg.get("BACKEND_API_BASE") or ""
    return base.rstrip("/")


def _auth_headers(content_type: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type

    cfg = get_config()
    auth_enabled = str(cfg.get("AUTH_ENABLED", "")).lower() in {"1", "true", "yes", "on"}
    if not auth_enabled:
        return headers

    token: Optional[str] = None
    try:
        from services import auth_session  # Local import to avoid circular at module import time

        cookie_name = f"{cfg.get('SESSION_COOKIE_NAME', 'assistant_session')}_api"
        token = auth_session.get_cookie(cookie_name)
    except Exception:
        token = None

    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_timeout() -> int:
    cfg = get_config()
    try:
        return int(cfg.get("REQUEST_TIMEOUT", 60))
    except (TypeError, ValueError):
        return 60


def upload_file(file, source: Optional[str] = None, tags: Optional[str] = None, lang_hint: Optional[str] = None) -> Dict[str, Any]:
    """Upload a single document using `POST /api/v1/uploads`.

    Contracts documented in ../../backend/docs/API_REFERENCE.md (Documents & Embeddings)
    and ../../backend/docs/API_ERRORS.md.
    """
    if not isinstance(file, tuple) or len(file) < 2:
        raise ValueError("file must be a (filename, data[, content_type]) tuple")

    filename = file[0]
    payload = file[1]
    content_type = file[2] if len(file) > 2 else "application/octet-stream"

    if isinstance(payload, (bytes, bytearray)):
        file_obj = io.BytesIO(payload)
    else:
        file_obj = payload
        try:
            file_obj.seek(0)
        except Exception:  # noqa: BLE001
            pass

    form_fields: Dict[str, str] = {}
    if source:
        form_fields["source"] = source
    if tags:
        form_fields["tags"] = tags
    if lang_hint:
        form_fields["lang_hint"] = lang_hint

    url = f"{_backend_base_url()}/api/v1/uploads"
    try:
        response = requests.post(
            url,
            data=form_fields or None,
            files={"file": (filename, file_obj, content_type)},
            headers=_auth_headers(),
            timeout=_request_timeout(),
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(0, "network_error", "Network error calling POST /api/v1/uploads", str(exc)) from exc
    finally:
        if isinstance(payload, (bytes, bytearray)):
            file_obj.close()

    if 200 <= response.status_code < 300:
        body = _json_or_text(response)
        if isinstance(body, dict):
            return body
        return {"raw": body}

    details = _json_or_text(response)
    status = response.status_code
    if status == 415:
        raise ApiError(415, "unsupported_media_type", "Format not allowed", details)
    if status == 413:
        raise ApiError(413, "payload_too_large", "File exceeds size limit", details)
    if status == 400:
        raise ApiError(400, "bad_request", "Invalid upload request", details)
    if status == 404:
        raise ApiError(404, "not_found", "Upload not found", details)
    if status == 409:
        raise ApiError(409, "conflict", "Upload conflict", details)
    raise ApiError(status, "api_error", "Unexpected API error", details)


def create_ingest_job(
    upload_ids: List[str],
    profile: str,
    tags: Optional[List[str]] = None,
    lang_hint: Optional[str] = None,
    priority: Optional[int] = None,
    update_alias: bool = False,
    evaluate: bool = False,
) -> Dict[str, Any]:
    """Create an ingestion job via `POST /api/v1/ingest/jobs`.

    Contracts documented in ../../backend/docs/API_REFERENCE.md (Documents & Embeddings)
    and ../../backend/docs/API_ERRORS.md.
    """
    payload: Dict[str, Any] = {
        "upload_ids": upload_ids,
        "profile": profile,
        "update_alias": update_alias,
        "evaluate": evaluate,
    }
    if tags:
        payload["tags"] = tags
    if lang_hint:
        payload["lang_hint"] = lang_hint
    if priority is not None:
        payload["priority"] = priority

    url = f"{_backend_base_url()}/api/v1/ingest/jobs"
    try:
        response = requests.post(
            url,
            json=payload,
            headers=_auth_headers("application/json"),
            timeout=_request_timeout(),
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(0, "network_error", "Network error calling POST /api/v1/ingest/jobs", str(exc)) from exc

    if 200 <= response.status_code < 300:
        body = _json_or_text(response)
        if isinstance(body, dict):
            return body
        return {"raw": body}

    details = _json_or_text(response)
    status = response.status_code
    if status == 422:
        raise ApiError(422, "validation_error", "Unknown profile", details)
    if status == 404:
        raise ApiError(404, "not_found", "Upload not found", details)
    if status == 409:
        raise ApiError(409, "conflict", "Conflicting job", details)
    if status >= 500:
        raise ApiError(status, "server_error", "Unable to create job", details)
    raise ApiError(status, "api_error", "Unexpected API error", details)


def get_job(job_id: str) -> Dict[str, Any]:
    """Fetch an ingestion job via `GET /api/v1/ingest/jobs/{job_id}`.

    Contracts documented in ../../backend/docs/API_REFERENCE.md (Documents & Embeddings)
    and ../../backend/docs/API_ERRORS.md.
    """
    url = f"{_backend_base_url()}/api/v1/ingest/jobs/{job_id}"
    try:
        response = requests.get(url, headers=_auth_headers(), timeout=_request_timeout())
    except Exception as exc:  # noqa: BLE001
        raise ApiError(0, "network_error", f"Network error calling GET /api/v1/ingest/jobs/{job_id}", str(exc)) from exc

    if 200 <= response.status_code < 300:
        body = _json_or_text(response)
        if isinstance(body, dict):
            return body
        return {"raw": body}

    details = _json_or_text(response)
    if response.status_code == 404:
        raise ApiError(404, "not_found", "Job not found", details)
    raise ApiError(response.status_code, "api_error", "Unexpected API error", details)


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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        debug_enabled = bool(get_config().get("DEBUG_CHAT_UI", False))
        chat_logger = logging.getLogger("chat_ui")
        out = {
            "answer": "",
            "answer2": None,
            "answer3": None,
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
            if debug_enabled:
                raw_keys = list(data.keys()) if isinstance(data, dict) else []
                len_answer = len((data.get("answer") or "")) if isinstance(data, dict) else 0
                try:
                    chat_logger.debug(
                        "API:chat_response_raw",
                        extra={"keys": raw_keys, "len_answer": len_answer, "type": type(data).__name__},
                    )
                except Exception:  # noqa: BLE001
                    print(f"API:chat_response_raw | keys={raw_keys} len_answer={len_answer} type={type(data).__name__}")
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
                        "answer3": parsed.get("answer3"),
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
                    "answer3": raw.get("answer3"),
                    "retrieved_chunks_metadata": raw.get("retrieved_chunks_metadata", []) or [],
                    "mode": raw.get("mode"),
                    "used_chunks": raw.get("used_chunks", []) or [],
                    "decision_explain": raw.get("decision_explain", {}) or {},
                })
            else:
                # Formato inesperado: intenta mapear campos del data base
                out["answer"] = data.get("answer", "") or ""
                out["answer2"] = data.get("answer2")
                out["answer3"] = data.get("answer3")
                out["retrieved_chunks_metadata"] = data.get("retrieved_chunks_metadata", []) or []
            if debug_enabled:
                decision = out.get("decision_explain") or {}
                summary = {
                    "keys": sorted(out.keys()),
                    "answer_type": type(out.get("answer")).__name__,
                    "answer_len": len(str(out.get("answer") or "")),
                    "answer2_type": type(out.get("answer2")).__name__,
                    "answer3_type": type(out.get("answer3")).__name__,
                    "mode": out.get("mode"),
                    "sim_max": decision.get("max_similarity"),
                    "used_chunks_count": len(out.get("used_chunks") or []),
                    "retrieved_chunks_count": len(out.get("retrieved_chunks_metadata") or []),
                }
                try:
                    chat_logger.debug("API:chat_response | %s", json.dumps(summary, default=str))
                except Exception:  # noqa: BLE001
                    print(f"API:chat_response | {summary}")
            return out
        except Exception as e:
            if debug_enabled:
                try:
                    chat_logger.exception("API:chat_response_error")
                except Exception:  # noqa: BLE001
                    print(f"API:chat_response_error | {e}")
            return {**out, "answer": f"Error contacting backend: {e}"}

    def send_feedback(
        self,
        *,
        user_id: Optional[int],
        session_id: str,
        rating: int,
        category: str,
        comment: str,
        metadata: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        debug_enabled = bool(get_config().get("DEBUG_CHAT_UI", False))
        chat_logger = logging.getLogger("chat_ui")

        body: Dict[str, Any] = {
            "rating": rating,
            "category": category,
            "comment": comment,
            "session_id": session_id,
            "metadata": metadata or {},
        }
        if user_id is not None:
            body["user_id"] = user_id
        body["metadata"]["client"] = "streamlit"
        body["metadata"]["ui_version"] = "chat-v2"
        if debug_enabled:
            try:
                chat_logger.debug(
                    "FEEDBACK:sending",
                    extra={
                        "message_id": message_id,
                        "rating": rating,
                        "category": category,
                    },
                )
            except Exception:  # noqa: BLE001
                print(f"FEEDBACK:sending | message_id={message_id} rating={rating} category={category}")

        def _post(body_data: Dict[str, Any]) -> Dict[str, Any]:
            return _request("POST", "/api/v1/feedback/", json_body=body_data)

        try:
            try:
                result = _post(body)
            except ApiError as err:
                status = getattr(err, "status", None)
                if status in {400, 422} and "created_at" in body:
                    if debug_enabled:
                        chat_logger.debug("FEEDBACK:retry_without_created_at", extra={"message_id": message_id, "status": status})
                    body_retry = dict(body)
                    body_retry.pop("created_at", None)
                    result = _post(body_retry)
                else:
                    raise RuntimeError(err.message or "Failed to submit feedback") from err
            if debug_enabled:
                chat_logger.debug("FEEDBACK:success", extra={"message_id": message_id})
            return result
        except Exception as exc:  # noqa: BLE001
            if debug_enabled:
                try:
                    chat_logger.exception("FEEDBACK:failed")
                except Exception:  # noqa: BLE001
                    print(f"FEEDBACK:failed | {exc}")
            raise RuntimeError(str(exc)) from exc
