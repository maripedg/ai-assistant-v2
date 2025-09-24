from typing import Dict, List, Optional, Tuple

from core.ports.chat_model import ChatModelPort
from core.ports.vector_store import VectorStorePort


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
        self.cfg = cfg

    def _select_chunks(self, doc_score_pairs: List[Tuple[object, float]]) -> List[dict]:
        """Sort results by score and drop duplicates based on metadata."""
        pairs = sorted(doc_score_pairs, key=lambda x: x[1], reverse=True)
        dedupe_key = self.cfg.get("retrieval", {}).get("dedupe_by", "doc_id")
        seen = set()
        selected: List[dict] = []
        for doc, score in pairs:
            meta = dict(doc.metadata)
            key = meta.get(dedupe_key) or meta.get("source") or meta.get("chunk_id")
            if key in seen:
                continue
            seen.add(key)
            meta["text"] = doc.page_content
            meta["score"] = float(score)
            selected.append(meta)
        return selected

    def _build_prompt(self, context_text: str, question: str) -> str:
        return (
            "Responde usando exclusivamente el contexto provisto. "
            "Si la informacion no esta en el contexto, responde que no se encontro evidencia.\n\n"
            f"[Contexto]\n{context_text}\n\n[Pregunta]\n{question}\n"
        )

    def _build_enrichment_prompt(self, base_answer: str) -> str:
        return (
            "Eres un asistente tecnico. Mejora la redaccion y estructura de la siguiente respuesta, "
            "agregando pasos claros y buenas practicas cuando aplique. No inventes datos.\n\n"
            f"[Respuesta]\n{base_answer}"
        )

    def answer(self, question: str) -> Dict:
        retrieval_cfg = self.cfg.get("retrieval", {})
        top_k = int(retrieval_cfg.get("top_k", 8))
        thr_low = float(retrieval_cfg.get("threshold_low", retrieval_cfg.get("similarity_threshold", 0.0)))
        thr_high = float(retrieval_cfg.get("threshold_high", thr_low))
        if thr_high < thr_low:
            thr_high = thr_low
        max_ctx = int(retrieval_cfg.get("max_context_chars", 6000))

        docs_scores = self.vs.similarity_search_with_score(question, k=top_k)
        if not docs_scores:
            return self._fallback_response(question, [])

        best_score = float(sorted(docs_scores, key=lambda x: x[1], reverse=True)[0][1])
        selected = self._select_chunks(docs_scores)

        if best_score >= thr_high:
            extractive_answer = self._build_extractive_answer(selected, max_ctx)
            return {
                "question": question,
                "answer": extractive_answer,
                "answer2": None,
                "answer3": None,
                "retrieved_chunks_metadata": selected,
            }

        if best_score >= thr_low:
            ctx = "\n\n".join(meta.get("text", "") for meta in selected)
            ctx = ctx[:max_ctx]
            rag_prompt = self._build_prompt(ctx, question)
            answer = (self.llm_primary.generate(rag_prompt) or "").strip()
            if not answer:
                return self._fallback_response(question, selected)

            answer2 = (self.llm_primary.generate(question) or "").strip() or None
            enrich_prompt = self._build_enrichment_prompt(answer)
            answer3 = (self.llm_primary.generate(enrich_prompt) or "").strip() or None
            return {
                "question": question,
                "answer": answer,
                "answer2": answer2,
                "answer3": answer3,
                "retrieved_chunks_metadata": selected,
            }

        return self._fallback_response(question, selected)

    def _fallback_response(self, question: str, selected: List[dict]) -> Dict:
        fallback_answer = (self.llm_fallback.generate(question) or "").strip()
        return {
            "question": question,
            "answer": "1" if fallback_answer else "",
            "answer2": fallback_answer or None,
            "answer3": fallback_answer or None,
            "retrieved_chunks_metadata": selected,
        }

    def _build_extractive_answer(self, selected: List[dict], max_chars: int) -> str:
        if not selected:
            return ""

        snippets: List[str] = []
        total = 0
        for idx, meta in enumerate(selected, 1):
            snippet = (meta.get("text", "") or "").strip()
            if not snippet:
                continue
            source = meta.get("source") or meta.get("doc_id") or meta.get("chunk_id")
            entry = f"{idx}. {snippet}"
            if source:
                entry += f" (source: {source})"
            snippets.append(entry)
            total = len("\n\n".join(snippets))
            if total >= max_chars:
                break

        extractive = "\n\n".join(snippets)
        return extractive[:max_chars]
