import re
from typing import Any, Dict, List, Optional, Tuple

from backend.core.ports.chat_model import ChatModelPort
from backend.core.ports.vector_store import VectorStorePort

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

        prompts_cfg = cfg.get("prompts", {}) or {}
        rag_cfg = prompts_cfg.get("rag", {}) or {}
        fallback_cfg = prompts_cfg.get("fallback", {}) or {}
        self._no_context_token = prompts_cfg.get("no_context_token", "__NO_CONTEXT__")
        self._rag_system_prompt = (rag_cfg.get("system") or "").strip()
        self._rag_style = rag_cfg.get("style") or "balanced"
        self._fallback_system_prompt = (fallback_cfg.get("system") or "").strip()

    def _select_chunks(self, doc_score_pairs: List[Tuple[object, float]]) -> List[dict]:
        """Sort results by similarity and drop duplicates based on metadata."""
        pairs = sorted(doc_score_pairs, key=lambda x: x[1], reverse=True)
        dedupe_key = self.cfg.get("retrieval", {}).get("dedupe_by", "doc_id")
        seen = set()
        selected: List[dict] = []
        for doc, score in pairs:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            key = metadata.get(dedupe_key) or metadata.get("source") or metadata.get("chunk_id")
            if key in seen:
                continue
            seen.add(key)
            metadata.setdefault("text", getattr(doc, "page_content", ""))
            selected.append(metadata)
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
        """Run retrieval, normalize scores, and choose the appropriate LLM."""
        retrieval_cfg = self.cfg.get("retrieval", {})
        top_k = int(retrieval_cfg.get("top_k", 8))
        base_thr_low = float(retrieval_cfg.get("threshold_low", retrieval_cfg.get("similarity_threshold", 0.0)))
        base_thr_high = float(retrieval_cfg.get("threshold_high", base_thr_low))
        if base_thr_high < base_thr_low:
            base_thr_high = base_thr_low
        max_ctx = int(retrieval_cfg.get("max_context_chars", 6000))

        question = (question or "").strip()

        short_cfg = retrieval_cfg.get("short_query", {}) or {}
        short_thr_low = float(short_cfg.get("threshold_low", base_thr_low))
        short_thr_high = float(short_cfg.get("threshold_high", base_thr_high))

        legacy_cfg = retrieval_cfg.get("legacy", {}) or {}
        rag_if_any_hit = bool(retrieval_cfg.get("rag_if_any_hit", False))

        cleaned = re.sub(r"[^\w\s]", " ", question.lower()).strip()
        tokens = [tok for tok in cleaned.split() if tok.isalpha()]
        stopwords = {"a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with", "is"}
        filtered_tokens = [tok for tok in tokens if tok not in stopwords]
        cleaned_compact_len = len(re.sub(r"\s+", "", cleaned))
        short_query_active = False
        if question:
            if len(filtered_tokens) <= 2 or cleaned_compact_len <= 5:
                short_query_active = True

        thr_low = short_thr_low if short_query_active else base_thr_low
        thr_high = short_thr_high if short_query_active else base_thr_high
        if thr_high < thr_low:
            thr_high = thr_low

        question_with_expansions = self._augment_question(question)

        raw_results = self.vs.similarity_search_with_score(question_with_expansions, k=top_k)
        score_mode = (getattr(self.vs, "_distance_label", "dot_product") or "dot_product").lower()
        normalization_metric = (legacy_cfg.get("store_metric") or score_mode).lower()

        if not raw_results:
            return self._fallback_response(
                question,
                [],
                None,
                thr_low,
                thr_high,
                top_k,
                question_with_expansions,
                short_query_active,
                bool(rag_if_any_hit),
            )

        processed_results: List[Tuple[Any, float]] = []
        similarities: List[float] = []

        for doc, raw_score in raw_results:
            raw_value = float(raw_score)
            similarity = self._normalize_score(raw_value, normalization_metric)
            metadata = dict(getattr(doc, "metadata", {}) or {})
            metadata["raw_score"] = raw_value
            metadata["similarity"] = similarity
            metadata["score"] = similarity
            metadata.setdefault("source", metadata.get("source") or "")
            metadata.setdefault("chunk_id", metadata.get("chunk_id") or "")
            doc.metadata = metadata
            processed_results.append((doc, similarity))
            similarities.append(similarity)

        docs_scores = processed_results
        max_similarity = max(similarities) if similarities else None
        selected = self._select_chunks(docs_scores)

        force_rag = bool(rag_if_any_hit and processed_results)

        decision_base = {
            "max_similarity": max_similarity,
            "threshold_low": thr_low,
            "threshold_high": thr_high,
            "top_k": top_k,
            "score_mode": "normalized",
            "effective_query": question_with_expansions,
            "short_query_active": short_query_active,
            "rag_if_any_hit": bool(rag_if_any_hit),
            "prompts": {
                "style": self._rag_style,
                "no_context_token": self._no_context_token,
            },
        }

        if not force_rag and max_similarity is not None and max_similarity >= thr_high:
            extractive_answer = self._build_extractive_answer(selected, max_ctx)
            decision = {**decision_base, "used_llm": "primary", "mode": "extractive"}
            return {
                "question": question,
                "answer": extractive_answer,
                "answer2": None,
                "answer3": None,
                "retrieved_chunks_metadata": selected,
                "mode": "extractive",
                "decision_explain": decision,
            }

        if force_rag or (max_similarity is not None and max_similarity >= thr_low):
            separator = "\n\n"
            query_terms = set(filtered_tokens) or set(tokens)
            selected_with_text = []
            for meta in selected:
                text_lower = (meta.get("text", "") or "").lower()
                selected_with_text.append((meta, text_lower))

            promoted_chunks: List[Dict[str, Any]] = []
            context_promotion = False
            ordered_meta: List[dict] = []
            chosen_ids = set()

            if query_terms:
                for meta, text_lower in selected_with_text:
                    if any(term and term in text_lower for term in query_terms):
                        ordered_meta.append(meta)
                        promoted_chunks.append({"source": meta.get("source") or "", "chunk_id": meta.get("chunk_id") or ""})
                        chosen_ids.add(id(meta))
                        context_promotion = True
                        break

            source_buckets: Dict[str, List[dict]] = {}
            source_order: List[str] = []
            for meta, _ in selected_with_text:
                if id(meta) in chosen_ids:
                    continue
                source = meta.get("source") or ""
                if source not in source_buckets:
                    source_buckets[source] = []
                    source_order.append(source)
                source_buckets[source].append(meta)

            while True:
                added = False
                for source in list(source_order):
                    bucket = source_buckets.get(source)
                    while bucket and id(bucket[0]) in chosen_ids:
                        bucket.pop(0)
                    if bucket:
                        candidate = bucket.pop(0)
                        ordered_meta.append(candidate)
                        chosen_ids.add(id(candidate))
                        added = True
                if not added:
                    break

            for meta, _ in selected_with_text:
                if id(meta) not in chosen_ids:
                    ordered_meta.append(meta)
                    chosen_ids.add(id(meta))

            context_parts: List[str] = []
            context_included: List[Dict[str, Any]] = []
            excluded_hits: List[Dict[str, Any]] = []
            remaining_bytes = max_ctx
            context_bytes_total = 0
            has_text = False

            for meta in ordered_meta:
                raw_text = (meta.get("text", "") or "").strip()
                if not raw_text:
                    continue
                source = meta.get("source") or ""
                chunk_id = meta.get("chunk_id") or ""
                encoded_text = raw_text.encode("utf-8")
                text_bytes = len(encoded_text)
                if remaining_bytes <= 0:
                    excluded_hits.append({"source": source, "chunk_id": chunk_id})
                    continue
                if has_text:
                    sep_bytes = len(separator.encode("utf-8"))
                    if sep_bytes > remaining_bytes:
                        excluded_hits.append({"source": source, "chunk_id": chunk_id})
                        continue
                    context_parts.append(separator)
                    remaining_bytes -= sep_bytes
                    context_bytes_total += sep_bytes
                if text_bytes <= remaining_bytes:
                    part_text = raw_text
                    used_bytes = text_bytes
                else:
                    part_text = encoded_text[:remaining_bytes].decode("utf-8", errors="ignore")
                    used_bytes = len(part_text.encode("utf-8"))
                if used_bytes == 0:
                    excluded_hits.append({"source": source, "chunk_id": chunk_id})
                    continue
                context_parts.append(part_text)
                remaining_bytes -= used_bytes
                context_bytes_total += used_bytes
                context_included.append({"source": source, "chunk_id": chunk_id, "bytes": used_bytes})
                has_text = True
                if remaining_bytes <= 0:
                    continue

            if context_promotion:
                included_pairs = {(entry["source"], entry["chunk_id"]) for entry in context_included}
                if not any((chunk["source"], chunk["chunk_id"]) in included_pairs for chunk in promoted_chunks):
                    context_promotion = False
                    promoted_chunks = []

            ctx = "".join(context_parts)
            rag_prompt_body = self._build_prompt(ctx, question)
            rag_prompt = f"{self._rag_system_prompt}\n\n{rag_prompt_body}" if self._rag_system_prompt else rag_prompt_body
            answer = (self.llm_primary.generate(rag_prompt) or "").strip()
            no_context_triggered = answer == self._no_context_token

            if no_context_triggered or not answer:
                return self._fallback_response(
                    question,
                    selected,
                    max_similarity,
                    thr_low,
                    thr_high,
                    top_k,
                    question_with_expansions,
                    short_query_active,
                    bool(rag_if_any_hit),
                    no_context_triggered=no_context_triggered,
                )

            answer2 = (self.llm_primary.generate(question) or "").strip() or None
            enrich_prompt = self._build_enrichment_prompt(answer)
            answer3 = (self.llm_primary.generate(enrich_prompt) or "").strip() or None
            decision = {**decision_base, "used_llm": "primary", "mode": "rag"}
            decision.update({
                "context_bytes_limit": max_ctx,
                "context_bytes_total": context_bytes_total,
                "context_included": context_included[:5],
                "excluded_top_hits": excluded_hits[:5],
                "context_promotion": context_promotion,
                "promoted_chunks": promoted_chunks[:5],
                "no_context_triggered": False,
            })
            return {
                "question": question,
                "answer": answer,
                "answer2": answer2,
                "answer3": answer3,
                "retrieved_chunks_metadata": selected,
                "mode": "rag",
                "decision_explain": decision,
            }

    def _fallback_response(
        self,
        question: str,
        selected: List[dict],
        max_similarity: Optional[float],
        thr_low: float,
        thr_high: float,
        top_k: int,
        effective_query: str,
        short_query_active: bool,
        rag_if_any_hit: bool,
        no_context_triggered: bool = False,
    ) -> Dict:
        fallback_prompt = question
        if self._fallback_system_prompt:
            fallback_prompt = f"{self._fallback_system_prompt}\n\n{question}"
        fallback_answer = (self.llm_fallback.generate(fallback_prompt) or "").strip()
        decision = {
            "max_similarity": max_similarity,
            "threshold_low": thr_low,
            "threshold_high": thr_high,
            "top_k": top_k,
            "used_llm": "fallback",
            "score_mode": "normalized",
            "effective_query": effective_query,
            "short_query_active": short_query_active,
            "rag_if_any_hit": bool(rag_if_any_hit),
            "prompts": {
                "style": self._rag_style,
                "no_context_token": self._no_context_token,
            },
            "no_context_triggered": bool(no_context_triggered),
            "mode": "fallback",
        }
        return {
            "question": question,
            "answer": "1" if fallback_answer else "",
            "answer2": fallback_answer or None,
            "answer3": fallback_answer or None,
            "retrieved_chunks_metadata": selected,
            "mode": "fallback",
            "decision_explain": decision,
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

    def _augment_question(self, question: str) -> str:
        expansions_cfg = self.cfg.get("retrieval", {}).get("expansions", {})
        if not expansions_cfg or not question:
            return question

        enabled = True
        terms_map: Dict[str, Any]
        if isinstance(expansions_cfg, dict):
            enabled = expansions_cfg.get("enabled", True)
            terms_map = expansions_cfg.get("terms", expansions_cfg)
        else:
            terms_map = expansions_cfg
        if not enabled or not isinstance(terms_map, dict):
            return question

        normalized = question.lower()
        additions: List[str] = []
        for key, phrases in terms_map.items():
            key_lower = str(key).lower().strip()
            if not key_lower or key_lower not in normalized:
                continue
            if isinstance(phrases, str):
                phrases = [phrases]
            for phrase in phrases or []:
                phrase = (phrase or "").strip()
                if phrase and phrase.lower() not in normalized:
                    additions.append(phrase)
        if not additions:
            return question
        return question + " " + " ".join(additions)

    def _normalize_score(self, raw_score: float, score_mode: str) -> float:
        mode = (score_mode or "dot_product").lower()
        if "dot" in mode:
            similarity = (raw_score + 1.0) / 2.0
        elif "cos" in mode:
            similarity = 1.0 - raw_score
        else:
            similarity = 1.0 / (1.0 + abs(raw_score))
        if similarity < 0.0:
            return 0.0
        if similarity > 1.0:
            return 1.0
        return similarity







