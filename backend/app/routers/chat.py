from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from backend.app.models.chat import ChatRequest
from backend.app import deps as app_deps
from backend.core.services.retrieval_service import RetrievalService

router = APIRouter()

# Instancias singleton simples (MVP)
app_deps.validate_startup()
_embeddings = app_deps.make_embeddings()
_vector = app_deps.make_vector_store(_embeddings)
_llm_primary = app_deps.make_chat_model_primary()
_llm_fallback = app_deps.make_chat_model_fallback()
_service = RetrievalService(_vector, _llm_primary, _llm_fallback, app_deps.settings.app)

@router.post("/chat")
def chat(req: ChatRequest, response: Response):
    result = _service.answer(req.question)
    mode = result.get("mode")
    if mode:
        response.headers["X-Answer-Mode"] = str(mode)
    return JSONResponse(content=result)
