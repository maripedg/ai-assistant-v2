from fastapi import APIRouter
from ..models.chat import ChatRequest, ChatResponse
from app.deps import settings, make_embeddings, make_vector_store, make_chat_model
from core.services.retrieval_service import RetrievalService

router = APIRouter()

# Instancias singleton simples (MVP)
_embeddings = make_embeddings()
_vector = make_vector_store(_embeddings)
_chat = make_chat_model()
_service = RetrievalService(_vector, _chat, settings.app)

@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    result = _service.answer(req.question)
    return result
