import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.core.ports.chat_model import ChatModelPort
from backend.core.ports.vector_store import VectorStorePort


def is_no_context_reply(text: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Returns (True, "exact_token") if exact token matches (respecting case_insensitive,
    strip_whitespace, max_chars_for_exact_token). Returns (True, f"regex:{pattern}")
    if any configured regex matches (case-insensitive when set). Else (False, "").
    """
    if not isinstance(cfg, dict) or not cfg.get("enabled", False):
        return False, ""

    val = text or ""
    precedence = cfg.get("precedence") or ["exact_token", "regex_phrases"]
    for rule in precedence:
        if rule == "exact_token":
            exact = cfg.get("exact_token") or {}
            token = (exact.get("value") or "").strip()
            if not token:
                continue
            s = val
            if exact.get("strip_whitespace", False):
                s = s.strip()
            max_len = int(cfg.get("max_chars_for_exact_token", 64) or 64)
            if len(s) <= max_len:
                if exact.get("case_insensitive", False):
                    if s.upper() == token.upper():
                        return True, "exact_token"
                else:
                    if s == token:
                        return True, "exact_token"
        elif rule == "regex_phrases":
            rex = cfg.get("regex_phrases") or {}
            pats = rex.get("patterns") or []
            if not isinstance(pats, list):
                pats = []
            flags = re.IGNORECASE if rex.get("case_insensitive", False) else 0
            for pat in pats:
                try:
                    if re.search(pat, val, flags=flags):
                        return True, f"regex:{pat}"
                except re.error:
                    continue
    return False, ""

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
        self.score_kind = (retrieval_cfg.get("score_kind") or "similarity").lower()
        # Default docs_normalized to True when missing
        self.docs_normalized = bool(retrieval_cfg.get("docs_normalized", True))

        # Require normalized thresholds
        if "low" not in thresholds_cfg or "high" not in thresholds_cfg:
            raise ValueError("retrieval.thresholds.low/high are required in app.yaml")
        self.norm_low = float(thresholds_cfg.get("low"))
        self.norm_high = float(thresholds_cfg.get("high"))

        # Validate RAW mode thresholds per metric
        if self.score_mode == "raw":
            if "dot" in self.distance:
                if "raw_dot_low" not in thresholds_cfg or "raw_dot_high" not in thresholds_cfg:
                    raise ValueError(
                        "retrieval.thresholds.raw_dot_low/high are required for score_mode=raw with distance=dot_product"
                    )
                self.raw_dot_low = float(thresholds_cfg.get("raw_dot_low"))
                self.raw_dot_high = float(thresholds_cfg.get("raw_dot_high"))
            elif "cos" in self.distance:
                if "raw_cosine_low" not in thresholds_cfg or "raw_cosine_high" not in thresholds_cfg:
                    raise ValueError(
                        "retrieval.thresholds.raw_cosine_low/high are required for score_mode=raw with distance=cosine"
                    )
                self.raw_cos_low = float(thresholds_cfg.get("raw_cosine_low"))
                self.raw_cos_high = float(thresholds_cfg.get("raw_cosine_high"))
            else:
                raise ValueError(
                    f"score_mode=raw unsupported for distance={self.distance} (explicit raw thresholds required)"
                )

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

        # No-context decision config
        self.llm_no_context_cfg = retrieval_cfg.get("llm_no_context", {}) or {}

        prompts_cfg = cfg.get("prompts", {}) or {}
        self.no_context_token = prompts_cfg.get("no_context_token", "__NO_CONTEXT__")
        self.rag_prompt = (prompts_cfg.get("rag", {}).get("system") or "").strip()
        self.hybrid_prompt = (prompts_cfg.get("hybrid", {}).get("system") or "").strip()
        self.fallback_prompt = (prompts_cfg.get("fallback", {}).get("system") or "").strip()

        self.max_ctx_chars = int(retrieval_cfg.get("max_context_chars", 6000))
        # Extra decision-explain fields (filled by helpers like select_context)
        self._extra_explain: Dict[str, Any] = {}

    def select_context(self, query: str) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
        """
        Select context chunks using adaptive thresholding and MMR with per-doc cap.

        Returns (selected_chunks, best3_chunks, explain_dict).
        - selected_chunks: up to 7 chunks after filtering and MMR (cap 2 per doc)
        - best3_chunks: top 3 by similarity from selected_chunks (for LLM)
        - explain_dict: {t_adapt, p90, sim_max, kept_n, cap_per_doc, mmr, gate_failed?}
        """
        raw_results = self.vs.similarity_search_with_score(query, k=self.top_k)
        # Build candidate list with normalized similarity
        candidates: List[Dict[str, Any]] = []
        for doc, raw_score in (raw_results or []):
            try:
                rv = float(raw_score)
            except Exception:
                rv = 0.0
            sim = self._normalize(rv)
            meta = dict(getattr(doc, "metadata", {}) or {})
            if "raw_score" not in meta:
                meta["raw_score"] = rv
            candidates.append({
                "doc": doc,
                "sim": sim,
                "raw_score": rv,
                "text": getattr(doc, "page_content", "") or "",
                "doc_id": meta.get("doc_id") or meta.get("source") or "",
                "chunk_id": meta.get("chunk_id") or "",
                "metadata": meta,
            })

        sims = [c["sim"] for c in candidates]
        sim_max = max(sims) if sims else 0.0

        def p90(values: List[float]) -> float:
            if not values:
                return 0.0
            vs = sorted(values)
            n = len(vs)
            if n == 1:
                return vs[0]
            pos = 0.9 * (n - 1)
            lo = math.floor(pos)
            hi = math.ceil(pos)
            if lo == hi:
                return vs[lo]
            frac = pos - lo
            return vs[lo] * (1 - frac) + vs[hi] * frac

        p90_val = p90(sims) if sims else 0.0
        if not sims:
            p90_val = 0.0
        t_adapt = max(self.hybrid_gate_min_similarity, (p90_val - 0.03))
        # Stricter threshold for short queries
        try:
            if self._is_short_query(query):
                t_adapt = min(1.0, t_adapt + 0.03)
        except Exception:
            pass

        # Filter by adaptive threshold
        filtered = [c for c in candidates if c["sim"] >= t_adapt]

        # Tokenize helper for diversity
        def _tokens(text: str) -> set:
            words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
            return {w for w in words if w.isalpha()}

        for c in filtered:
            c["tokens"] = _tokens(c["text"])  # type: ignore[assignment]

        # MMR selection with per-doc cap
        lam = 0.30
        per_doc_cap = 2
        max_keep = 7
        selected: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}

        # Start from highest similarity
        pool = sorted(filtered, key=lambda x: x["sim"], reverse=True)

        def pair_sim(a: Dict[str, Any], b: Dict[str, Any]) -> float:
            at = a.get("tokens") or set()
            bt = b.get("tokens") or set()
            if not at or not bt:
                return 0.0
            inter = len(at & bt)
            if inter == 0:
                return 0.0
            uni = len(at | bt)
            return inter / float(uni or 1)

        while pool and len(selected) < max_keep:
            best = None
            best_score = -1e9
            for cand in pool:
                doc_key = cand.get("doc_id") or ""
                if counts.get(doc_key, 0) >= per_doc_cap:
                    continue
                if not selected:
                    score = cand["sim"]
                else:
                    max_div = 0.0
                    for s in selected:
                        max_div = max(max_div, pair_sim(cand, s))
                    score = lam * cand["sim"] - (1.0 - lam) * max_div
                if score > best_score:
                    best_score = score
                    best = cand
            if best is None:
                break
            best["mmr_score"] = float(best_score)
            selected.append(best)
            dk = best.get("doc_id") or ""
            counts[dk] = counts.get(dk, 0) + 1
            # Remove the chosen item from pool
            pool = [c for c in pool if c is not best]

        # Take top-3 by similarity from selected
        by_similarity = sorted(selected, key=lambda x: x["sim"], reverse=True)
        best3 = by_similarity[:3]
        # Preserve best-7 order by final MMR score
        best7 = sorted(selected, key=lambda x: x.get("mmr_score", x["sim"]), reverse=True)

        # Gates
        gate_failed: Optional[str] = None
        if sim_max < self.hybrid_gate_min_similarity:
            gate_failed = "min_similarity_gate"
        elif len(best3) < self.hybrid_gate_min_chunks:
            gate_failed = "min_chunks_gate"
        else:
            total_bytes = 0
            for idx, item in enumerate(best3):
                t = (item.get("text") or "").encode("utf-8")
                total_bytes += len(t)
                if idx > 0:
                    total_bytes += len(b"\n\n")
            if total_bytes < self.hybrid_gate_min_total_chars:
                gate_failed = "min_total_chars_gate"

        explain = {
            "t_adapt": float(t_adapt),
            "p90": float(p90_val),
            "sim_max": float(sim_max),
            "kept_n": int(len(selected)),
            "cap_per_doc": per_doc_cap,
            "mmr": True,
            "hybrid_candidates": int(len(best7)),
            "hybrid_sent": int(len(best3)) if not gate_failed else 0,
        }
        if gate_failed:
            explain["gate_failed"] = gate_failed

        def _payload(item: Dict[str, Any]) -> Dict[str, Any]:
            payload = {
                "doc": item.get("doc"),
                "metadata": dict(item.get("metadata") or {}),
                "text": item.get("text") or "",
                "similarity": float(item.get("sim", 0.0)),
                "mmr_score": float(item.get("mmr_score", item.get("sim", 0.0))),
                "raw_score": float(item.get("raw_score", 0.0)),
            }
            return payload

        best7_payload = [_payload(c) for c in best7]
        best3_payload = [] if gate_failed else [_payload(c) for c in best3]
        return best7_payload, best3_payload, explain

    def answer(self, question: str) -> Dict:
        question = (question or "").strip()
        log.debug("retrieval question=%s", question[:120])
        self._extra_explain = {}

        short_query = self._is_short_query(question)

        def _update_extra(**updates: Any) -> None:
            extra = dict(self._extra_explain or {})
            extra.update(updates)
            self._extra_explain = extra

        raw_results = self.vs.similarity_search_with_score(question, k=self.top_k)
        if not raw_results:
            _update_extra(hybrid_candidates=0, hybrid_sent=0, gate_failed=None, fallback_reason=None)
            return self._build_response(question, "fallback", [], [], None, short_query, llm_used="fallback")

        metas = self._build_metas(raw_results)
        if not metas:
            _update_extra(hybrid_candidates=0, hybrid_sent=0, gate_failed=None, fallback_reason=None)
            return self._build_response(question, "fallback", [], [], None, short_query, llm_used="fallback")

        for idx, meta in enumerate(metas, start=1):
            sim_val = float(meta.get("similarity", 0.0))
            if sim_val < 0.0:
                sim_val = 0.0
            elif sim_val > 1.0:
                sim_val = 1.0
            meta["similarity"] = sim_val
            meta["rank"] = idx
            if "raw_score" in meta:
                meta["raw_score"] = float(meta["raw_score"])

        max_raw = max(m["raw_score"] for m in metas)
        max_norm = max(m["similarity"] for m in metas)

        decision_score, low, high = self._pick_thresholds(max_raw, max_norm, short_query)
        mode = self._decide_mode(decision_score, low, high, short_query)

        raw_vals = [m["raw_score"] for m in metas]
        sim_vals = [m["similarity"] for m in metas]
        log.info(
            "raw_range=[%.4f..%.4f] sim_range=[%.4f..%.4f] metric=%s kind=%s docs_norm=%s",
            min(raw_vals),
            max(raw_vals),
            min(sim_vals),
            max(sim_vals),
            self.distance,
            self.score_kind,
            self.docs_normalized,
        )

        if mode == "fallback":
            _update_extra(hybrid_candidates=0, hybrid_sent=0, gate_failed=None, fallback_reason=None)
            return self._build_response(question, mode, metas, [], decision_score, short_query, llm_used="fallback")

        # Drive decision via select_context for hybrid-or-fallback
        selected_docs, best3_docs, explain = self.select_context(question)
        best7_count = len(selected_docs)
        best3_count = len(best3_docs)
        extra = dict(explain or {})
        if "gate_failed" not in extra:
            extra["gate_failed"] = None
        if "fallback_reason" not in extra:
            extra["fallback_reason"] = None
        _update_extra(**extra)
        _update_extra(hybrid_candidates=int(best7_count), hybrid_sent=int(best3_count))

        # Always attach best-7 metadata
        retrieved_metas: List[Dict[str, Any]] = []
        for idx, payload in enumerate(selected_docs, start=1):
            doc = payload.get("doc")
            meta = dict(payload.get("metadata") or {})
            if not meta and doc is not None:
                meta = dict(getattr(doc, "metadata", {}) or {})
            similarity = float(payload.get("similarity", 0.0))
            if similarity < 0.0:
                similarity = 0.0
            elif similarity > 1.0:
                similarity = 1.0
            meta["similarity"] = similarity
            meta["rank"] = idx
            raw_val = payload.get("raw_score")
            if raw_val is not None:
                meta["raw_score"] = float(raw_val)
            retrieved_metas.append(meta)

        if not best3_docs and explain.get("gate_failed"):
            gate_name = explain.get("gate_failed")
            fallback_reason = f"gate_failed:{gate_name}" if gate_name else None
            _update_extra(
                gate_failed=gate_name,
                fallback_reason=fallback_reason,
                hybrid_candidates=int(best7_count),
                hybrid_sent=0,
            )
            return self._build_response(
                question,
                "fallback",
                retrieved_metas,
                [],
                decision_score,
                short_query,
                llm_used="fallback",
                reason=fallback_reason,
            )

        # Build context and used_chunks from best3_docs
        parts: List[str] = []
        uchunks: List[Dict[str, Any]] = []
        for d in best3_docs:
            doc = d.get("doc")
            meta = dict(d.get("metadata") or {})
            if not meta and doc is not None:
                meta = dict(getattr(doc, "metadata", {}) or {})
            text = (d.get("text") or getattr(doc, "page_content", "") or "").strip()
            parts.append(text)
            if "raw_score" not in meta and d.get("raw_score") is not None:
                meta["raw_score"] = float(d["raw_score"])
            sim_val = float(d.get("similarity", 0.0))
            if sim_val < 0.0:
                sim_val = 0.0
            elif sim_val > 1.0:
                sim_val = 1.0
            uchunks.append({
                "chunk_id": meta.get("chunk_id", ""),
                "source": meta.get("source", ""),
                "score": float(sim_val),
                "snippet": text[:320],
            })
        context_text = "\n\n".join(parts)
        used_chunks = uchunks
        # Track used chunks in decision_explain
        try:
            extra = dict(self._extra_explain or {})
            extra["used_chunks"] = used_chunks
            self._extra_explain = extra
        except Exception:
            pass
        if not context_text:
            gate_name = "min_total_chars_gate"
            fallback_reason = f"gate_failed:{gate_name}"
            _update_extra(
                gate_failed=gate_name,
                fallback_reason=fallback_reason,
                hybrid_candidates=int(best7_count),
                hybrid_sent=0,
            )
            return self._build_response(
                question,
                "fallback",
                retrieved_metas,
                [],
                decision_score,
                short_query,
                llm_used="fallback",
                reason=fallback_reason,
            )

        # Evidence gate: only applicable for hybrid vs fallback
        if mode == "hybrid":
            total_ctx_chars = len(context_text.encode("utf-8"))
            if decision_score is not None and decision_score < self.hybrid_gate_min_similarity:
                gate_name = "min_similarity_gate"
                fallback_reason = f"gate_failed:{gate_name}"
                _update_extra(
                    gate_failed=gate_name,
                    fallback_reason=fallback_reason,
                    hybrid_candidates=int(best7_count),
                    hybrid_sent=0,
                )
                return self._build_response(
                    question,
                    "fallback",
                    retrieved_metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason=fallback_reason,
                )
            if len(used_chunks) < self.hybrid_gate_min_chunks:
                gate_name = "min_chunks_gate"
                fallback_reason = f"gate_failed:{gate_name}"
                _update_extra(
                    gate_failed=gate_name,
                    fallback_reason=fallback_reason,
                    hybrid_candidates=int(best7_count),
                    hybrid_sent=0,
                )
                return self._build_response(
                    question,
                    "fallback",
                    retrieved_metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason=fallback_reason,
                )
            if total_ctx_chars < self.hybrid_gate_min_total_chars:
                gate_name = "min_total_chars_gate"
                fallback_reason = f"gate_failed:{gate_name}"
                _update_extra(
                    gate_failed=gate_name,
                    fallback_reason=fallback_reason,
                    hybrid_candidates=int(best7_count),
                    hybrid_sent=0,
                )
                return self._build_response(
                    question,
                    "fallback",
                    retrieved_metas,
                    [],
                    decision_score,
                    short_query,
                    llm_used="fallback",
                    reason=fallback_reason,
                )

        system_prompt = self.rag_prompt
        if True:  # hybrid path driven by select_context
            base_prompt = self.hybrid_prompt or self.rag_prompt
            instruction = "If the provided context is insufficient to answer safely, reply with the single token: NO_CONTEXT"
            system_prompt = f"{base_prompt}\n{instruction}" if base_prompt else instruction
        prompt = self._compose_prompt(system_prompt, context_text, question)

        answer = (self.llm_primary.generate(prompt) or "").strip()
        ans_clean = (answer or "").strip()
        # Check explicit no-context token handling from config
        flag, rule = is_no_context_reply(ans_clean, self.llm_no_context_cfg)
        if (not ans_clean) or flag or (ans_clean == (self.no_context_token or "")):
            # mark llm_returned when applicable
            try:
                extra = dict(self._extra_explain or {})
                if flag or ans_clean.upper() == "NO_CONTEXT":
                    extra["llm_returned"] = "NO_CONTEXT"
                if flag and rule:
                    extra["no_context_rule"] = rule
                self._extra_explain = extra
            except Exception:
                pass
            _update_extra(
                gate_failed=None,
                fallback_reason="llm_returned_no_context",
                hybrid_candidates=int(best7_count),
                hybrid_sent=int(best3_count),
            )
            return self._build_response(
                question,
                "fallback",
                retrieved_metas,
                [],
                decision_score,
                short_query,
                llm_used="fallback",
                reason="llm_returned_no_context",
            )

        _update_extra(
            gate_failed=None,
            fallback_reason=None,
            hybrid_candidates=int(best7_count),
            hybrid_sent=int(best3_count),
        )
        return self._build_response(
            question,
            "hybrid",
            retrieved_metas,
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
        # Cohere embeddings are unit-normalized when docs_normalized=True.
        # Normalization rules:
        # - dot + docs_normalized=True       -> (-raw + 1) / 2   [VECTOR_DISTANCE(DOT) returns distance]
        # - cosine + score_kind=similarity   -> (raw + 1) / 2
        # - cosine + score_kind=distance     -> 1 - clamp(raw/2, 0, 1)
        # - l2/distance fallback             -> 1 / (1 + |raw|)
        if "dot" in self.distance and self.docs_normalized:
            value = (-raw_value + 1.0) / 2.0
        elif "cos" in self.distance:
            if self.score_kind == "similarity":
                value = (raw_value + 1.0) / 2.0
            else:
                x = raw_value / 2.0
                if x < 0.0:
                    x = 0.0
                elif x > 1.0:
                    x = 1.0
                value = 1.0 - x
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
                raise ValueError("raw mode requires explicit raw thresholds for the selected metric")
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
        extra = getattr(self, "_extra_explain", None)
        if isinstance(extra, dict) and extra:
            try:
                decision_explain.update(extra)
            finally:
                self._extra_explain = {}
        decision_explain.setdefault("hybrid_candidates", 0)
        decision_explain.setdefault("hybrid_sent", 0)
        decision_explain.setdefault("gate_failed", None)
        decision_explain.setdefault("fallback_reason", None)

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
