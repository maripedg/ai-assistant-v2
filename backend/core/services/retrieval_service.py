import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.core.ports.chat_model import ChatModelPort
from backend.core.ports.vector_store import VectorStorePort

log = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self,
        vector_store: VectorStorePort,
        primary_llm: ChatModelPort,
        fallback_llm: Optional[ChatModelPort],
        cfg: dict,
    ) -> None:
        self.vs = vector_store
        self.llm_primary = primary_llm
        self.llm_fallback = fallback_llm or primary_llm
        retrieval_cfg = cfg.get("retrieval", {}) or {}

        thresholds_cfg = retrieval_cfg.get("thresholds", {}) or {}
        self.score_mode = (retrieval_cfg.get("score_mode") or "normalized").lower()
        self.distance = (retrieval_cfg.get("distance") or "dot_product").lower()

        self.norm_low = float(thresholds_cfg.get("low", 0.20))
        self.norm_high = float(thresholds_cfg.get("high", 0.45))
        self.raw_dot_low = float(thresholds_cfg.get("raw_dot_low", -0.50))
        self.raw_dot_high = float(thresholds_cfg.get("raw_dot_high", -0.20))
        self.raw_cos_low = float(thresholds_cfg.get("raw_cosine_low", 0.25))
        self.raw_cos_high = float(thresholds_cfg.get("raw_cosine_high", 0.55))

        short_cfg = retrieval_cfg.get("short_query", {}) or {}
        self.short_max_tokens = int(short_cfg.get("max_tokens", 2))
        self.short_low = float(short_cfg.get("threshold_low", self.norm_low))
        self.short_high = float(short_cfg.get("threshold_high", self.norm_high))

        hybrid_cfg = retrieval_cfg.get("hybrid", {}) or {}
        self.hybrid_max_chars = int(hybrid_cfg.get("max_context_chars", 8000))
        self.hybrid_max_chunks = int(hybrid_cfg.get("max_chunks", 6))
        self.hybrid_min_chars = int(hybrid_cfg.get("min_tokens_per_chunk", 200))
        # Evidence gate defaults
        self.hybrid_gate_min_similarity = float(hybrid_cfg.get("min_similarity_for_hybrid", 0.0))
        self.hybrid_gate_min_chunks = int(hybrid_cfg.get("min_chunks_for_hybrid", 0))
        self.hybrid_gate_min_total_chars = int(hybrid_cfg.get("min_total_context_chars", 0))

        self.top_k = int(retrieval_cfg.get("top_k", 8))
        self.dedupe_key = retrieval_cfg.get("dedupe_by", "doc_id")

        prompts_cfg = cfg.get("prompts", {}) or {}
        self.no_context_token = prompts_cfg.get("no_context_token", "__NO_CONTEXT__")
        self.rag_prompt = (prompts_cfg.get("rag", {}).get("system") or "").strip()
        self.hybrid_prompt = (prompts_cfg.get("hybrid", {}).get("system") or "").strip()
        self.fallback_prompt = (prompts_cfg.get("fallback", {}).get("system") or "").strip()

        self.max_ctx_chars = int(retrieval_cfg.get("max_context_chars", 6000))

    def answer(self, question: str) -> Dict:
        question = (question or "").strip()
        log.debug("retrieval question=%s", question[:120])

        short_query = self._is_short_query(question)

        raw_results = self.vs.similarity_search_with_score(question, k=self.top_k)
        if not raw_results:
            return self._build_response(question, "fallback", [], [], None, short_query, llm_used="fallback")

        metas = self._build_metas(raw_results)
        if not metas:
            return self._build_response(question, "fallback", [], [], None, short_query, llm_used="fallback")

        max_raw = max(m["raw_score"] for m in metas)
        max_norm = max(m["similarity"] for m in metas)

        decision_score, low, high = self._pick_thresholds(max_raw, max_norm, short_query)
        mode = self._decide_mode(decision_score, low, high, short_query)

        if mode == "fallback":
            return self._build_response(question, mode, metas, [], decision_score, short_query, llm_used="fallback")

        context_text, used_chunks = self._select_context(metas)
        if not context_text:
            return self._build_response(
                question,
                "fallback",
                metas,
                [],
                decision_score,
                short_query,
                llm_used="fallback",
                reason="gate_failed_min_context",
            )

        # Evidence gate: only applicable for hybrid vs fallback
        if mode == "hybrid":
            total_ctx_chars = len(context_text.encode("utf-8"))
            if decision_score is not None and decision_score < self.hybrid_gate_min_similarity:
                return self._build_response(
                    question,
                    "fallback",
                    metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason="gate_failed_min_similarity",
                )
            if len(used_chunks) < self.hybrid_gate_min_chunks:
                return self._build_response(
                    question,
                    "fallback",
                    metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason="gate_failed_min_chunks",
                )
            if total_ctx_chars < self.hybrid_gate_min_total_chars:
                return self._build_response(
                    question,
                    "fallback",
                    metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason="gate_failed_min_context",
                )

        system_prompt = self.rag_prompt
        if mode == "hybrid":
            system_prompt = self.hybrid_prompt or self.rag_prompt
        prompt = self._compose_prompt(system_prompt, context_text, question)

        answer = (self.llm_primary.generate(prompt) or "").strip()
        if not answer or answer == (self.no_context_token or ""):
            return self._build_response(
                question,
                "fallback",
                metas,
                [],
                decision_score,
                short_query,
                llm_used="fallback",
                reason="llm_no_context_token" if answer == (self.no_context_token or "") else None,
            )

        return self._build_response(
            question,
            mode,
            metas,
            used_chunks,
            decision_score,
            short_query,
            answer=answer,
            llm_used="primary",
        )

    def _is_short_query(self, question: str) -> bool:
        cleaned = re.sub(r"[^\w\s]", " ", question.lower()).strip()
        tokens = [tok for tok in cleaned.split() if tok.isalpha()]
        return len(tokens) <= self.short_max_tokens

    def _build_metas(self, raw_results) -> List[Dict[str, Any]]:
        metas: List[Dict[str, Any]] = []
        for doc, raw_score in raw_results:
            raw_value = float(raw_score)
            similarity = self._normalize(raw_value)
            meta = dict(getattr(doc, "metadata", {}) or {})
            metas.append({
                "text": getattr(doc, "page_content", ""),
                "source": meta.get("source", ""),
                "doc_id": meta.get("doc_id", ""),
                "chunk_id": meta.get("chunk_id", ""),
                "raw_score": raw_value,
                "similarity": similarity,
            })
        return metas

    def _normalize(self, raw_value: float) -> float:
        if "dot" in self.distance:
            value = (raw_value + 1.0) / 2.0
        elif "cos" in self.distance:
            value = 1.0 - raw_value
        else:
            value = 1.0 / (1.0 + abs(raw_value))
        return min(max(value, 0.0), 1.0)

    def _pick_thresholds(self, max_raw: Optional[float], max_norm: Optional[float], short_query: bool):
        if self.score_mode == "raw":
            score = max_raw if max_raw is not None else float("-inf")
            if "dot" in self.distance:
                low, high = self.raw_dot_low, self.raw_dot_high
            elif "cos" in self.distance:
                low, high = self.raw_cos_low, self.raw_cos_high
            else:
                low, high = self.norm_low, self.norm_high
        else:
            score = max_norm if max_norm is not None else float("-inf")
            low, high = self.norm_low, self.norm_high
        if short_query:
            low, high = self.short_low, self.short_high
        return score, low, high

    def _decide_mode(self, score: float, low: float, high: float, short_query: bool) -> str:
        if score >= high:
            mode = "rag"
        elif score >= low:
            mode = "hybrid"
        else:
            mode = "fallback"
        log.info(
            "decision: mode=%s score_mode=%s distance=%s max=%.3f low=%.3f high=%.3f short=%s",
            mode,
            self.score_mode,
            self.distance,
            score,
            low,
            high,
            short_query,
        )
        return mode

    def _select_context(self, metas: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        scored = sorted(metas, key=lambda m: m["similarity"], reverse=True)
        kept: List[Dict[str, Any]] = []
        dedupe: set = set()
        total_bytes = 0
        for meta in scored:
            key = meta.get(self.dedupe_key) or meta.get("source") or meta.get("chunk_id")
            if key in dedupe:
                continue
            text = (meta.get("text") or "").strip()
            if len(text) < self.hybrid_min_chars:
                continue
            chunk_bytes = len(text.encode("utf-8"))
            extra = len(b"\n\n") if kept else 0
            if kept and total_bytes + extra + chunk_bytes > self.hybrid_max_chars:
                break
            kept.append(meta)
            dedupe.add(key)
            total_bytes += chunk_bytes + extra
            if len(kept) >= self.hybrid_max_chunks:
                break
        context_text = "\n\n".join((m.get("text") or "").strip() for m in kept)
        used_chunks = [
            {
                "chunk_id": m.get("chunk_id", ""),
                "source": m.get("source", ""),
                "score": float(m.get("similarity")),
                "snippet": (m.get("text") or "").strip()[:320],
            }
            for m in kept
        ]
        return context_text, used_chunks

    def _compose_prompt(self, system_prompt: str, context: str, question: str) -> str:
        body = f"[Context]\n{context}\n\n[Question]\n{question}" if context else f"[Question]\n{question}"
        return f"{system_prompt}\n\n{body}" if system_prompt else body


    def _build_response(
        self,
        question: str,
        mode: str,
        metas: List[Dict[str, Any]],
        used_chunks: List[Dict[str, Any]],
        decision_score: Optional[float],
        short_query: bool,
        answer: Optional[str] = None,
        llm_used: str = "primary",
        reason: Optional[str] = None,
    ) -> Dict:
        if not answer:
            prompt = f"{self.fallback_prompt}\n\n{question}" if self.fallback_prompt else question
            answer = (self.llm_fallback.generate(prompt) or "").strip()
            llm_used = "fallback"
            mode = "fallback"
            used_chunks = []

        sources_used = "none"
        if mode == "rag" and used_chunks:
            sources_used = "all"
        elif mode == "hybrid" and used_chunks:
            sources_used = "partial"

        if short_query:
            threshold_low = self.short_low
            threshold_high = self.short_high
        elif self.score_mode == "raw" and "dot" in self.distance:
            threshold_low = self.raw_dot_low
            threshold_high = self.raw_dot_high
        elif self.score_mode == "raw" and "cos" in self.distance:
            threshold_low = self.raw_cos_low
            threshold_high = self.raw_cos_high
        else:
            threshold_low = self.norm_low
            threshold_high = self.norm_high

        decision_explain = {
            "score_mode": self.score_mode,
            "distance": self.distance,
            "max_similarity": float(decision_score) if decision_score is not None else float("-inf"),
            "threshold_low": threshold_low,
            "threshold_high": threshold_high,
            "top_k": self.top_k,
            "short_query_active": bool(short_query),
            "mode": mode,
            "effective_query": question,
            "used_llm": llm_used,
        }
        if reason:
            decision_explain["reason"] = reason

        return {
            "question": question,
            "answer": answer or "",
            "answer2": None,
            "answer3": None,
            "retrieved_chunks_metadata": metas,
            "mode": mode,
            "sources_used": sources_used,
            "used_chunks": used_chunks,
            "decision_explain": decision_explain,
        }
