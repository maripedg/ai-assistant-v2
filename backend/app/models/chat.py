from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str
    user_id: Optional[int] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class DecisionExplain(BaseModel):
    max_similarity: Optional[float]
    threshold_low: float
    threshold_high: float
    top_k: int
    effective_query: str
    short_query_active: bool
    used_llm: Literal["primary", "fallback"]
    mode: Literal["extractive", "rag", "hybrid", "fallback"]
    score_mode: Literal["normalized", "raw"]
    distance: Optional[str] = None


class UsedChunk(BaseModel):
    chunk_id: str
    source: str
    score: float
    snippet: str


class ChatResponse(BaseModel):
    question: str
    answer: str
    answer2: Optional[str] = None
    answer3: Optional[str] = None
    retrieved_chunks_metadata: List[Dict]
    mode: Literal["extractive", "rag", "hybrid", "fallback"]
    sources_used: str
    used_chunks: List[UsedChunk]
    decision_explain: DecisionExplain
