from typing import Literal, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str


class DecisionExplain(BaseModel):
    max_similarity: Optional[float]
    threshold_low: float
    threshold_high: float
    top_k: int
    effective_query: str
    short_query_active: bool
    used_llm: Literal["primary", "fallback"]
    mode: Literal["extractive", "rag", "fallback"]
    score_mode: Literal["normalized"]


class ChatResponse(BaseModel):
    question: str
    answer: str
    answer2: Optional[str] = None
    answer3: Optional[str] = None
    retrieved_chunks_metadata: list
    mode: Literal["extractive", "rag", "fallback"]
    decision_explain: DecisionExplain
