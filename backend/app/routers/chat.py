import logging
from decimal import Decimal
from threading import Lock
from time import perf_counter
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from backend.app.models.chat import ChatRequest
from backend.app import deps as app_deps
from backend.app.config import usage_log_enabled
from backend.core.db.session import session_scope
from backend.core.repos.usage_repo_db import UsageRepoDB
from backend.core.services.retrieval_service import RetrievalService

router = APIRouter()
logger = logging.getLogger(__name__)

# Instancias singleton simples (MVP)
app_deps.validate_startup()
_embeddings = app_deps.make_embeddings()
_llm_primary = app_deps.make_chat_model_primary()
_llm_fallback = app_deps.make_chat_model_fallback()
_service_lock = Lock()
_service: Optional[RetrievalService] = None
_cached_vector_id: Optional[int] = None


def _vector_dependency():
    return app_deps.get_vector_store_safe(_embeddings)


def _get_service(vector_store) -> RetrievalService:
    global _service, _cached_vector_id
    with _service_lock:
        if _service is None or _cached_vector_id != id(vector_store):
            _service = RetrievalService(vector_store, _llm_primary, _llm_fallback, app_deps.settings.app)
            _cached_vector_id = id(vector_store)
        return _service


@router.post("/chat")
def chat(
    req: ChatRequest,
    response: Response,
    request: Request,
    vector_store=Depends(_vector_dependency),
    current_user: Optional[Any] = Depends(app_deps.get_current_user_optional),
):
    if vector_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store unavailable. Please try again shortly.",
        )

    service = _get_service(vector_store)
    start_ts = perf_counter()
    result = service.answer(req.question)
    latency_ms = int((perf_counter() - start_ts) * 1000.0)
    mode = result.get("mode")
    if mode:
        response.headers["X-Answer-Mode"] = str(mode)
    if usage_log_enabled():
        try:
            _log_chat_usage(req, request, result, current_user, latency_ms)
        except Exception as exc:  # noqa: BLE001
            logger.debug("usage.log_interaction skipped (%s)", exc.__class__.__name__)
    return JSONResponse(content=result)


def _log_chat_usage(req: ChatRequest, request: Request, result: dict, current_user: Optional[Any], latency_ms: int) -> None:
    _ = current_user
    metadata = getattr(req, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    client = metadata.get("client") or request.headers.get("x-client-app") or "streamlit"
    ui_version = metadata.get("ui_version") or request.headers.get("x-ui-version") or request.headers.get("x-app-version") or "chat-v2"
    session_id = getattr(req, "session_id", None) or request.headers.get("x-session-id")
    message_id = getattr(req, "message_id", None) or result.get("message_id")
    question_text = getattr(req, "question", None)
    answer_preview = (result.get("answer") or "")[:600]
    resp_mode = result.get("mode")
    sources_count = len(result.get("retrieved_chunks_metadata") or [])
    max_similarity = _to_float(result.get("decision_explain", {}), "max_similarity")
    tokens_prompt = _to_int(result.get("tokens_prompt"))
    tokens_completion = _to_int(result.get("tokens_completion"))
    cost_usd = _to_decimal(result.get("cost_usd"))
    feedback_id = _to_int(result.get("feedback_id"))
    user_id = _to_int(getattr(req, "user_id", None))

    with session_scope() as usage_db:
        if session_id:
            UsageRepoDB.upsert_session(
                usage_db,
                session_id=session_id,
                user_id=user_id,
                client=str(client),
                ui_version=ui_version,
            )
        UsageRepoDB.log_interaction(
            usage_db,
            session_id=session_id,
            user_id=user_id,
            message_id=message_id,
            question_text=question_text,
            answer_preview=answer_preview,
            resp_mode=resp_mode,
            sources_count=sources_count,
            max_similarity=max_similarity,
            latency_ms=latency_ms,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            cost_usd=cost_usd,
            feedback_id=feedback_id,
            client=str(client),
            ui_version=ui_version,
        )


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(source: Any, key: str) -> Optional[float]:
    data = source or {}
    if isinstance(data, dict):
        value = data.get(key)
    else:
        value = None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
