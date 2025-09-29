from fastapi import APIRouter, Response

from ..models.chat import ChatRequest, ChatResponse
from app.deps import (
    settings,
    make_embeddings,
    make_vector_store,
    make_chat_model_primary,
    make_chat_model_fallback,
    validate_startup,
)
from core.services.retrieval_service import RetrievalService

router = APIRouter()

# Instancias singleton simples (MVP)
validate_startup()
_embeddings = make_embeddings()
_vector = make_vector_store(_embeddings)
_llm_primary = make_chat_model_primary()
_llm_fallback = make_chat_model_fallback()
_service = RetrievalService(_vector, _llm_primary, _llm_fallback, settings.app)

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, response: Response):
    result = _service.answer(req.question)
    mode = result.get("mode")
    if mode:
        response.headers["X-Answer-Mode"] = str(mode)
    return result
