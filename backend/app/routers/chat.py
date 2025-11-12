from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse

from backend.app.models.chat import ChatRequest
from backend.app import deps as app_deps
from backend.core.services.retrieval_service import RetrievalService

router = APIRouter()

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
def chat(req: ChatRequest, response: Response, vector_store=Depends(_vector_dependency)):
    if vector_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store unavailable. Please try again shortly.",
        )

    service = _get_service(vector_store)
    result = service.answer(req.question)
    mode = result.get("mode")
    if mode:
        response.headers["X-Answer-Mode"] = str(mode)
    return JSONResponse(content=result)
