import logging
logger = logging.getLogger(__name__)
import math
import re
import os
import json
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
DEBUG_RETRIEVAL_METADATA = (os.getenv("DEBUG_RETRIEVAL_METADATA") or "false").lower() in {"1", "true", "yes", "on"}


def _preview(val: Any, limit: int = 200) -> str:
    try:
        s = str(val)
    except Exception:
        return ""
    return s[:limit]


def _dbg(label: str, obj: Any) -> None:
    if not DEBUG_RETRIEVAL_METADATA:
        return
    try:
        if isinstance(obj, dict):
            log.info("DEBUG_METADATA %s type=dict keys=%s", label, list(obj.keys()))
            return
        if isinstance(obj, (list, tuple)):
            log.info(
                "DEBUG_METADATA %s type=%s len=%d first_type=%s",
                label,
                type(obj).__name__,
                len(obj),
                type(obj[0]).__name__ if obj else None,
            )
            if obj:
                _dbg(f"{label}[0]", obj[0])
            return
        if hasattr(obj, "metadata"):
            meta = getattr(obj, "metadata", None)
            meta_info = None
            if isinstance(meta, dict):
                meta_info = list(meta.keys())
            elif isinstance(meta, str):
                meta_info = f"str:{_preview(meta)}"
            else:
                meta_info = type(meta).__name__
            fields = list(getattr(obj, "__dict__", {}).keys())
            log.info(
                "DEBUG_METADATA %s type=%s fields=%s meta_info=%s",
                label,
                type(obj).__name__,
                fields,
                meta_info,
            )
            return
        log.info("DEBUG_METADATA %s type=%s preview=%s", label, type(obj).__name__, _preview(obj))
    except Exception:
        return


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
        exclude_cfg = hybrid_cfg.get("exclude_chunk_types_from_llm")
        default_exclude = ["figure"]
        if isinstance(exclude_cfg, list) and exclude_cfg:
            self.exclude_chunk_types_from_llm = [str(x).lower() for x in exclude_cfg]
        else:
            self.exclude_chunk_types_from_llm = default_exclude

        prompts_cfg = cfg.get("prompts", {}) or {}
        self.no_context_token = prompts_cfg.get("no_context_token", "__NO_CONTEXT__")
        self.rag_prompt = (prompts_cfg.get("rag", {}).get("system") or "").strip()
        self.hybrid_prompt = (prompts_cfg.get("hybrid", {}).get("system") or "").strip()
        self.fallback_prompt = (prompts_cfg.get("fallback", {}).get("system") or "").strip()

        self.max_ctx_chars = int(retrieval_cfg.get("max_context_chars", 6000))
        # Extra decision-explain fields (filled by helpers like select_context)
        self._extra_explain: Dict[str, Any] = {}
        embeddings_cfg = cfg.get("embeddings") if isinstance(cfg, dict) else {}
        alias_cfg = embeddings_cfg.get("alias") if isinstance(embeddings_cfg, dict) else {}
        alias_name = alias_cfg.get("name") if isinstance(alias_cfg, dict) else None
        self.default_alias_view = alias_name.strip() if isinstance(alias_name, str) else None

    def _resolve_metadata(self, doc: Any, row_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Merge metadata from vector document and optional row dict, preserving chunk/source ids.
        """
        meta: Dict[str, Any] = {}
        if isinstance(row_meta, dict):
            meta = dict(row_meta)
        elif isinstance(row_meta, str):
            try:
                parsed = json.loads(row_meta)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:  # noqa: BLE001
                meta = {}
        doc_meta = getattr(doc, "metadata", None)
        if isinstance(doc_meta, dict) and doc_meta:
            merged = dict(meta or {})
            merged.update(doc_meta)
            meta = merged
        elif isinstance(doc_meta, str):
            try:
                parsed_doc = json.loads(doc_meta)
                if isinstance(parsed_doc, dict):
                    merged = dict(meta or {})
                    merged.update(parsed_doc)
                    meta = merged
            except Exception:  # noqa: BLE001
                pass

        meta = dict(meta or {})
        row_fields: Dict[str, Any] = {}
        if isinstance(row_meta, dict):
            row_fields.update(row_meta)
        if isinstance(doc, dict):
            row_fields.update(doc)
        for key in ("id", "chunk_id", "doc_id", "source"):
            val = getattr(doc, key, None)
            if val is not None:
                row_fields.setdefault(key, val)

        chunk_id = meta.get("chunk_id") or row_fields.get("chunk_id") or row_fields.get("id") or ""
        source = (
            meta.get("source")
            or meta.get("doc_id")
            or row_fields.get("source")
            or row_fields.get("doc_id")
            or ""
        )
        doc_id = meta.get("doc_id") or row_fields.get("doc_id")

        if chunk_id:
            meta["chunk_id"] = chunk_id
        if source:
            meta["source"] = source
        if doc_id:
            meta["doc_id"] = doc_id

        return meta

    def _is_excluded_from_llm_context(self, meta: Dict[str, Any]) -> bool:
        ctype = str(meta.get("chunk_type") or "").lower()
        if ctype and ctype in self.exclude_chunk_types_from_llm:
            return True
        return meta.get("block_type") == "image"

    def select_context(
        self,
        query: str,
        *,
        target_view: Optional[str] = None,
        raw_results: Optional[List[Any]] = None,
    ) -> Tuple[List[Any], List[Any], Dict[str, Any]]:
        """
        Select context chunks using adaptive thresholding and MMR with per-doc cap.

        Returns (selected_chunks, best3_chunks, explain_dict).
        - selected_chunks: up to max_keep chunks after filtering and MMR (cap per_doc_cap per doc)
        - best3_chunks: top 3 by similarity from selected_chunks (for LLM)
        - explain_dict: {t_adapt, p90, sim_max, kept_n, cap_per_doc, mmr, gate_failed?}
        """
        vector_kwargs = {"target_view": target_view} if target_view else {}
        if raw_results is None:
            k_overfetch = max(self.top_k, self.top_k * 4)
            raw_results = self.vs.similarity_search_with_score(query, k=k_overfetch, **vector_kwargs)
        if DEBUG_RETRIEVAL_METADATA:
            _dbg("VECTORSTORE_RETURN", raw_results)
        # Build candidate list with normalized similarity
        candidates: List[Dict[str, Any]] = []
        for idx, (doc, raw_score) in enumerate(raw_results or []):
            if DEBUG_RETRIEVAL_METADATA and idx < 2:
                raw_meta = None
                keys: Any = None
                if isinstance(doc, dict):
                    keys = list(doc.keys())
                    raw_meta = doc.get("METADATA") or doc.get("metadata")
                elif isinstance(doc, (list, tuple)):
                    keys = list(range(len(doc)))
                else:
                    keys = list(getattr(doc, "__dict__", {}).keys())
                    raw_meta = getattr(doc, "metadata", None)
                meta_type = type(raw_meta).__name__ if raw_meta is not None else "None"
                preview = ""
                if isinstance(raw_meta, str):
                    preview = raw_meta[:200]
                elif isinstance(raw_meta, dict):
                    preview = str(list(raw_meta.keys()))[:200]
                elif raw_meta is not None:
                    preview = str(raw_meta)[:200]
                log.info(
                    "DEBUG_METADATA raw_result idx=%d type=%s keys=%s meta_type=%s meta_preview=%s",
                    idx,
                    type(doc).__name__,
                    keys,
                    meta_type,
                    preview,
                )
            try:
                rv = float(raw_score)
            except Exception:
                rv = 0.0
            sim = self._normalize(rv)
            meta = self._resolve_metadata(doc)
            if DEBUG_RETRIEVAL_METADATA and idx < 2:
                meta_keys = list(meta.keys())
                log.info(
                    "DEBUG_METADATA parsed_meta idx=%d chunk_id=%s source=%s doc_id=%s meta_keys=%s",
                    idx,
                    meta.get("chunk_id"),
                    meta.get("source"),
                    meta.get("doc_id"),
                    meta_keys,
                )
            if "raw_score" not in meta:
                meta["raw_score"] = rv
            candidates.append(
                {
                    "doc": doc,
                    "sim": sim,
                    "raw_score": rv,
                    "text": getattr(doc, "page_content", "") or "",
                    "doc_id": meta.get("doc_id") or meta.get("source") or "",
                    "chunk_id": meta.get("chunk_id") or "",
                    "metadata": meta,
                }
            )

        if DEBUG_RETRIEVAL_METADATA and candidates:
            _dbg("AFTER_MAPPING", candidates[:2])
            for j, cand in enumerate(candidates[:2]):
                meta = cand.get("metadata") or {}
                log.info(
                    "DEBUG_METADATA mapped_candidate idx=%d source=%s chunk_id=%s doc_id=%s",
                    j,
                    meta.get("source"),
                    meta.get("chunk_id"),
                    meta.get("doc_id"),
                )

        # 🔍 DEBUG: loguear TODOS los candidatos crudos antes de filtros / MMR
        if candidates:
            sorted_cands = sorted(candidates, key=lambda x: x["sim"], reverse=True)
            log.info(
                "=== RAW VECTOR CANDIDATES (top_k=%d) for query: %s ===",
                len(sorted_cands),
                (query or "").replace("\n", " ")[:200],
            )
            for idx, c in enumerate(sorted_cands, start=1):
                meta = c.get("metadata") or {}
                snippet = (c.get("text") or "").replace("\n", " ")[:200]
                log.info(
                    "  #%02d sim=%.4f raw=%.4f doc_id=%s chunk_id=%s source=%s snippet=%s",
                    idx,
                    float(c.get("sim", 0.0)),
                    float(c.get("raw_score", 0.0)),
                    c.get("doc_id", ""),
                    c.get("chunk_id", ""),
                    meta.get("source", ""),
                    snippet,
                )

        llm_eligible_candidates: List[Dict[str, Any]] = []
        excluded_candidates: List[Dict[str, Any]] = []
        for cand in candidates:
            meta_tmp = self._resolve_metadata(cand.get("doc"), cand.get("metadata"))
            if self._is_excluded_from_llm_context(meta_tmp):
                excluded_candidates.append(cand)
            else:
                llm_eligible_candidates.append(cand)

        sims_eligible = [c["sim"] for c in llm_eligible_candidates]
        sim_max_eligible = max(sims_eligible) if sims_eligible else 0.0

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

        p90_val = p90(sims_eligible) if sims_eligible else 0.0
        if not sims_eligible:
            p90_val = 0.0
        t_adapt = max(self.hybrid_gate_min_similarity, (p90_val - 0.03))
        # Stricter threshold for short queries
        try:
            if self._is_short_query(query):
                t_adapt = min(1.0, t_adapt + 0.03)
        except Exception:
            pass

        # Filter by adaptive threshold (eligible-only), keep a minimum text pool
        ranked_text_all = sorted(llm_eligible_candidates, key=lambda x: x["sim"], reverse=True)
        filtered_eligible = [c for c in ranked_text_all if c["sim"] >= t_adapt]
        min_text_keep = max(6, self.hybrid_max_chunks)
        if len(filtered_eligible) < min_text_keep:
            filtered_eligible = ranked_text_all[:min_text_keep]

        # Tokenize helper for diversity
        def _tokens(text: str) -> set:
            words = re.sub(r"[^\w\s]", " ", (text or "").lower()).split()
            return {w for w in words if w.isalpha()}

        for c in filtered_eligible:
            c["tokens"] = _tokens(c["text"])  # type: ignore[assignment]

        # MMR selection with per-doc cap
        lam = 0.30
        per_doc_cap = 6
        max_keep = 12

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

        def mmr_select(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            selected_local: List[Dict[str, Any]] = []
            counts_local: Dict[str, int] = {}
            pool_local = sorted(candidates, key=lambda x: x["sim"], reverse=True)
            while pool_local and len(selected_local) < max_keep:
                best = None
                best_score = -1e9
                for cand in pool_local:
                    doc_key = cand.get("doc_id") or ""
                    if counts_local.get(doc_key, 0) >= per_doc_cap:
                        continue
                    if not selected_local:
                        score = cand["sim"]
                    else:
                        max_div = 0.0
                        for s in selected_local:
                            max_div = max(max_div, pair_sim(cand, s))
                        score = lam * cand["sim"] - (1.0 - lam) * max_div
                    if score > best_score:
                        best_score = score
                        best = cand
                if best is None:
                    break
                best["mmr_score"] = float(best_score)
                selected_local.append(best)
                dk = best.get("doc_id") or ""
                counts_local[dk] = counts_local.get(dk, 0) + 1
                pool_local = [c for c in pool_local if c is not best]
            return selected_local

        selected_text = mmr_select(filtered_eligible)

        # Select LLM context candidates by similarity from text-only MMR set
        ranked_text = sorted(selected_text, key=lambda x: x["sim"], reverse=True)
        best3: List[Dict[str, Any]] = []
        total_bytes = 0
        for cand in ranked_text:
            text = (cand.get("text") or getattr(cand.get("doc"), "page_content", "") or "").strip()
            if not text:
                continue
            chunk_bytes = len(text.encode("utf-8"))
            extra = len(b"\n\n") if best3 else 0
            if chunk_bytes + extra > self.hybrid_max_chars and not best3:
                continue
            if best3 and total_bytes + extra + chunk_bytes > self.hybrid_max_chars:
                continue
            best3.append(cand)
            total_bytes += chunk_bytes + extra
            if len(best3) >= self.hybrid_max_chunks:
                break
        # Preserve best-7 order by final MMR score (aquí ahora es hasta max_keep, pero el nombre se mantiene)
        excluded_ranked = sorted(excluded_candidates, key=lambda x: x["sim"], reverse=True)
        best7 = (selected_text + excluded_ranked)[:max_keep]

        # Gates
        gate_failed: Optional[str] = None
        if sim_max_eligible < self.hybrid_gate_min_similarity:
            gate_failed = "min_similarity_gate"
        elif len(best3) < self.hybrid_gate_min_chunks:
            gate_failed = "min_chunks_gate"
        else:
            if total_bytes < self.hybrid_gate_min_total_chars:
                gate_failed = "min_total_chars_gate"

        explain = {
            "t_adapt": float(t_adapt),
            "p90": float(p90_val),
            "sim_max": float(sim_max_eligible),
            "kept_n": int(len(selected_text)),
            "cap_per_doc": per_doc_cap,
            "mmr": True,
            "hybrid_candidates": int(len(best7)),
            "hybrid_sent": int(len(best3)) if not gate_failed else 0,
            "ranked_candidates_total": int(len(candidates)),
            "eligible_candidates_total": int(len(llm_eligible_candidates)),
            "excluded_candidates_total": int(len(excluded_candidates)),
            "text_after_cap_total": int(len(selected_text)),
            "selected_for_llm_count": int(len(best3)),
            "final_context_chars": int(total_bytes),
        }
        if gate_failed:
            explain["gate_failed"] = gate_failed
        if DEBUG_RETRIEVAL_METADATA:
            log.info(
                "DEBUG_METADATA llm_selection ranked_candidates_total=%s eligible_candidates_total=%s excluded_candidates_total=%s "
                "text_after_cap_total=%s selected_for_llm_count=%s final_context_chars=%s p90_eligible=%s t_adapt=%s sim_max_eligible=%s",
                explain.get("ranked_candidates_total"),
                explain.get("eligible_candidates_total"),
                explain.get("excluded_candidates_total"),
                explain.get("text_after_cap_total"),
                explain.get("selected_for_llm_count"),
                explain.get("final_context_chars"),
                explain.get("p90"),
                explain.get("t_adapt"),
                explain.get("sim_max"),
            )

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

    def answer(self, question: str, *, target_view: Optional[str] = None) -> Dict:
        question = (question or "").strip()
        log.debug("retrieval question=%s", question[:120])
        self._extra_explain = {}

        short_query = self._is_short_query(question)

        def _update_extra(**updates: Any) -> None:
            extra = dict(self._extra_explain or {})
            extra.update(updates)
            self._extra_explain = extra

        effective_target = target_view or self.default_alias_view
        _update_extra(retrieval_target=effective_target)

        vector_kwargs = {"target_view": effective_target} if effective_target else {}
        k_overfetch = max(self.top_k, self.top_k * 4)
        raw_results = self.vs.similarity_search_with_score(question, k=k_overfetch, **vector_kwargs)
        if DEBUG_RETRIEVAL_METADATA:
            _dbg("VECTORSTORE_RETURN_ANSWER", raw_results)
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

        eligible_sims = [
            m["similarity"] for m in metas if not self._is_excluded_from_llm_context(m)
        ]
        max_norm = max(eligible_sims) if eligible_sims else float("-inf")
        max_raw = max(m["raw_score"] for m in metas) if metas else float("-inf")

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
        selected_docs, best3_docs, explain = self.select_context(
            question,
            target_view=effective_target,
            raw_results=raw_results,
        )
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
            meta = self._resolve_metadata(doc, payload.get("metadata"))
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

        if DEBUG_RETRIEVAL_METADATA and retrieved_metas:
            _dbg("BEFORE_RESPONSE_metas", retrieved_metas[:2])
            for j, meta in enumerate(retrieved_metas[:2]):
                log.info(
                    "DEBUG_METADATA before_response_meta idx=%d source=%s chunk_id=%s doc_id=%s",
                    j,
                    meta.get("source"),
                    meta.get("chunk_id"),
                    meta.get("doc_id"),
                )

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
            meta = self._resolve_metadata(doc, d.get("metadata"))
            text = (d.get("text") or getattr(doc, "page_content", "") or "").strip()
            parts.append(text)
            if "raw_score" not in meta and d.get("raw_score") is not None:
                meta["raw_score"] = float(d["raw_score"])
            sim_val = float(d.get("similarity", 0.0))
            if sim_val < 0.0:
                sim_val = 0.0
            elif sim_val > 1.0:
                sim_val = 1.0
            uchunks.append(
                {
                    "chunk_id": meta.get("chunk_id", ""),
                    "source": meta.get("source", ""),
                    "score": float(sim_val),
                    "snippet": text[:320],
                }
            )
        if DEBUG_RETRIEVAL_METADATA:
            log.info(
                "DEBUG_METADATA context_selection selected=%s sent_to_llm=%s",
                len(best3_docs),
                len(uchunks),
            )
        if DEBUG_RETRIEVAL_METADATA and uchunks:
            _dbg("BEFORE_RESPONSE_used_chunks", uchunks[:2])
            for idx, ch in enumerate(uchunks[:2]):
                log.info(
                    "DEBUG_METADATA used_chunk idx=%d chunk_id=%s source=%s",
                    idx,
                    ch.get("chunk_id"),
                    ch.get("source"),
                )
        context_text = "\n\n".join(parts)
        used_chunks = uchunks
        best3_sent = len(uchunks)
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
                hybrid_sent=int(best3_sent),
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
            hybrid_sent=int(best3_sent),
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
            meta = self._resolve_metadata(doc)
            meta_out = dict(meta or {})
            meta_out["text"] = getattr(doc, "page_content", "")
            meta_out["raw_score"] = raw_value
            meta_out["similarity"] = similarity
            meta_out["source"] = meta_out.get("source") or ""
            meta_out["doc_id"] = meta_out.get("doc_id") or ""
            meta_out["chunk_id"] = meta_out.get("chunk_id") or ""
            metas.append(meta_out)
        if DEBUG_RETRIEVAL_METADATA and metas:
            for idx, m in enumerate(metas[:2]):
                log.info(
                    "DEBUG_METADATA _build_metas idx=%d chunk_id=%s source=%s doc_id=%s keys=%s",
                    idx,
                    m.get("chunk_id"),
                    m.get("source"),
                    m.get("doc_id"),
                    list(m.keys()),
                )
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
