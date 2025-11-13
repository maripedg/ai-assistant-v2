# backend/providers/oci/embeddings_adapter.py
from __future__ import annotations

import inspect
import os
import random
import time
from typing import List, Tuple, Dict, Any
import logging
import math
import re
import pathlib

logger = logging.getLogger(__name__)

_EMBED_MAX_RETRIES = 6  # 1 try + up to 5 retries
_EMBED_MIN_BATCH = 4    # lower bound when reducing batch size on throttling
from langchain_core.embeddings import Embeddings
import oci

try:
    # LangChain client for OCI GenAI
    from langchain_community.embeddings import OCIGenAIEmbeddings
except Exception as exc:  # pragma: no cover
    raise

class OCIEmbeddingsAdapter(Embeddings):
    """
    Adapter que implementa la interfaz de LangChain (Embeddings).
    Internamente delega en OCIGenAIEmbeddings y, si la versión lo permite,
    pasa input_type para mantener la asimetría query/document.
    """

    def __init__(
        self,
        model_id: str,
        service_endpoint: str,
        compartment_id: str,
        auth_file_location: str,
        auth_profile: str,
        doc_input_type: str = "search_document",
        query_input_type: str = "search_query",
    ) -> None:
        # Store config
        self._model_id = model_id
        self._endpoint = service_endpoint
        self._compartment_id = compartment_id
        self._doc_input_type = doc_input_type or "search_document"
        self._query_input_type = query_input_type or "search_query"
        # Token-limit handling config (loaded from app.yaml if present)
        self._max_input_tokens = 512
        self._on_token_limit = "split"  # split | truncate | skip
        self._token_estimator = "auto"   # auto | heuristic
        self._load_token_limit_config()
        # Metrics
        self.errors_token_limit = 0
        self.token_limit_splits = 0
        self.token_limit_truncations = 0
        self.skipped_token_limit = 0
        # Ensure OCI SDK picks up the desired config file/profile as a baseline
        if auth_file_location:
            os.environ["OCI_CONFIG_FILE"] = auth_file_location
        if auth_profile:
            os.environ["OCI_CONFIG_PROFILE"] = auth_profile
        # Build OCI Generative AI client using explicit file+profile
        cfg = oci.config.from_file(
            file_location=os.environ.get("OCI_CONFIG_FILE"),
            profile_name=os.environ.get("OCI_CONFIG_PROFILE", "DEFAULT"),
        )
        self._client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=cfg,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
            timeout=(10, 240),
            service_endpoint=self._endpoint,
        )
        # SDK models import and signature detection
        from oci.generative_ai_inference import models as _models
        self._models = _models
        self._details_init_params = set(
            inspect.signature(_models.EmbedTextDetails.__init__).parameters.keys()
        )

    # Methods expected by LangChain:

    def embed_documents(self, texts: List[str], input_type: str | None = None):
        # Determine serving mode
        if self._model_id.startswith("ocid1.generativeaiendpoint"):
            serving_mode = self._models.DedicatedServingMode(endpoint_id=self._model_id)
        else:
            serving_mode = self._models.OnDemandServingMode(model_id=self._model_id)

        embeddings: List[List[float]] = []
        index_map: List[int] = []
        batch_size = 96
        for i in range(0, len(texts), batch_size):
            original_batch = texts[i : i + batch_size]
            # Preflight transform each text to fit within token budget
            expanded, exp_map = self._preflight_expand_batch(original_batch)
            # Perform provider call with retry on token-limit 400s (tracking mapping)
            flat_vecs, out_map = self._embed_with_retry(serving_mode, expanded, input_type or self._doc_input_type, exp_map)
            # Reassemble per original position (average when split, empty vector when skipped)
            batch_vecs = self._reassemble_by_map(flat_vecs, out_map, len(original_batch))
            for local_idx, vec in enumerate(batch_vecs):
                if not isinstance(vec, list) or not vec:
                    continue
                embeddings.append(vec)
                index_map.append(i + local_idx)
        return embeddings, index_map

    def embed_query(self, text: str, input_type: str | None = None) -> List[float]:
        # Same handling as documents but with single item
        if self._model_id.startswith("ocid1.generativeaiendpoint"):
            serving_mode = self._models.DedicatedServingMode(endpoint_id=self._model_id)
        else:
            serving_mode = self._models.OnDemandServingMode(model_id=self._model_id)
        expanded, exp_map = self._preflight_expand_batch([text])
        vecs, out_map = self._embed_with_retry(serving_mode, expanded, input_type or self._query_input_type, exp_map)
        reassembled = self._reassemble_by_map(vecs, out_map, 1)
        return reassembled[0] if reassembled else []

    # ---------- Token handling helpers ----------
    def _load_token_limit_config(self) -> None:
        try:
            # Resolve app.yaml from repo layout (backend/config/app.yaml)
            here = pathlib.Path(__file__).resolve()
            app_yaml = here.parents[2] / "config" / "app.yaml"
            import yaml  # type: ignore
            data = {}
            if app_yaml.exists():
                with app_yaml.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            emb = (data.get("embeddings") or {}) if isinstance(data, dict) else {}
            active = (emb.get("active_profile") or "legacy_profile") if isinstance(emb, dict) else "legacy_profile"
            profiles = emb.get("profiles") or {}
            prof = profiles.get(active) or {}
            self._max_input_tokens = int(prof.get("max_input_tokens", 512) or 512)
            self._on_token_limit = str(prof.get("on_token_limit", "split") or "split").lower()
            self._token_estimator = str(prof.get("token_estimator", "auto") or "auto").lower()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Token-limit config load failed; using defaults: %s", exc)

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._token_estimator == "auto":
            try:
                import tiktoken  # type: ignore

                try:
                    enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
                except Exception:
                    enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                pass
        # Heuristic fallback
        return int(math.ceil(len(text) / 4))

    def _split_text_to_token_budget(self, text: str, max_tokens: int) -> List[str]:
        if not text:
            return [""]
        import re as _re

        sentences = _re.split(r"(?<=[.!?])\s+", text)
        chunks: List[str] = []
        current: List[str] = []
        cur_tokens = 0
        for sent in sentences:
            t = self._estimate_tokens(sent)
            if t > max_tokens:
                # Fallback: word window inside this long sentence
                words = sent.split()
                wbuf: List[str] = []
                wtok = 0
                for w in words:
                    wt = self._estimate_tokens((wbuf + [w]) and (" ".join(wbuf + [w])))
                    if wt <= max_tokens:
                        wbuf.append(w)
                        wtok = wt
                    else:
                        if wbuf:
                            chunks.append(" ".join(wbuf))
                        # If a single word is too big (rare), hard truncate the word
                        if self._estimate_tokens(w) > max_tokens:
                            hard = self._truncate_to_budget(w, max_tokens)
                            chunks.append(hard)
                        else:
                            wbuf = [w]
                            wtok = self._estimate_tokens(w)
                        wbuf = []
                        wtok = 0
                if wbuf:
                    chunks.append(" ".join(wbuf))
                continue
            if cur_tokens + t <= max_tokens:
                current.append(sent)
                cur_tokens += t
            else:
                if current:
                    chunks.append(" ".join(current))
                current = [sent]
                cur_tokens = t
        if current:
            chunks.append(" ".join(current))
        return [c for c in chunks if c]

    def _truncate_to_budget(self, text: str, max_tokens: int) -> str:
        if not text:
            return ""
        # Binary search over character length guided by token estimate
        lo, hi = 0, len(text)
        best = ""
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = text[:mid]
            t = self._estimate_tokens(candidate)
            if t <= max_tokens:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        if best and best != text:
            try:
                before = self._estimate_tokens(text)
                after = self._estimate_tokens(best)
                logger.warning("Token limit: truncate from ~%d → %d tokens", before, after)
                self.token_limit_truncations += 1
            except Exception:
                self.token_limit_truncations += 1
        return best or text[: max(1, min(len(text), int(max_tokens * 4)))]

    def _preflight_expand_batch(self, batch: List[str]) -> Tuple[List[str], List[int]]:
        """
        Returns (expanded_texts, grouping) where grouping maps each original index to
        indices in the expanded_texts. If an item is skipped, it maps to [].
        """
        expanded: List[str] = []
        exp_map: List[int] = []  # maps expanded index -> original index
        for idx, text in enumerate(batch):
            t = self._estimate_tokens(text)
            if t <= self._max_input_tokens:
                expanded.append(text)
                exp_map.append(idx)
                continue
            action = self._on_token_limit
            if action == "split":
                parts = self._split_text_to_token_budget(text, self._max_input_tokens)
                if parts:
                    logger.warning("Token limit: split item[%d] into %d parts (<=%d tokens each)", idx, len(parts), self._max_input_tokens)
                    self.token_limit_splits += len(parts)
                    expanded.extend(parts)
                    exp_map.extend([idx] * len(parts))
                else:
                    logger.warning("Token limit: skip item[%d] (~%d tokens)", idx, t)
                    self.skipped_token_limit += 1
            elif action == "truncate":
                trimmed = self._truncate_to_budget(text, self._max_input_tokens)
                expanded.append(trimmed)
                exp_map.append(idx)
            else:  # skip
                logger.warning("Token limit: skip item[%d] (~%d tokens)", idx, t)
                self.skipped_token_limit += 1
        return expanded, exp_map

    def _embed_with_retry(self, serving_mode, inputs: List[str], input_type: str | None, exp_map: List[int]) -> Tuple[List[List[float]], List[int]]:
        if not inputs:
            return [], []

        flat_vecs: List[List[float]] = []
        vec_map: List[int] = []

        configured = getattr(self, "_batch_size", 32) or 32
        batch_size = max(int(configured), _EMBED_MIN_BATCH)

        total = len(inputs)
        idx = 0

        while idx < total:
            chunk_end = min(total, idx + batch_size)
            chunk = list(inputs[idx:chunk_end])
            chunk_map = list(exp_map[idx:chunk_end])
            span = chunk_end - idx
            if not chunk or not chunk_map:
                idx += span
                continue

            attempt = 0
            while chunk:
                attempt += 1
                try:
                    details = self._build_embed_payload(chunk, input_type, serving_mode)
                    resp = self._client.embed_text(details)
                    vectors = self._extract_vectors(resp)
                    if not vectors:
                        logger.warning("Embedding call returned no vectors for chunk span=%s", span)
                    for local_idx, vector in enumerate(vectors):
                        if local_idx >= len(chunk_map):
                            break
                        if not isinstance(vector, list) or not vector:
                            continue
                        flat_vecs.append(vector)
                        vec_map.append(chunk_map[local_idx])
                    break
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    idx_match = re.search(r"texts\[(\d+)\] is too long", msg)
                    status = getattr(exc, "status", getattr(exc, "status_code", None))
                    status_400 = ("400" in msg) or (status == 400)
                    if status_400 and idx_match:
                        self.errors_token_limit += 1
                        bad_idx = int(idx_match.group(1))
                        logger.warning(
                            "Token limit provider error on chunk item %d (attempt %d); applying strategy",
                            bad_idx,
                            attempt,
                        )
                        chunk, chunk_map = self._repair_bad_index_with_map(chunk, chunk_map, bad_idx)
                        if not chunk:
                            logger.warning("Token limit handling removed entire chunk span=%s; skipping", span)
                            break
                        continue

                    code = getattr(exc, "code", None)
                    status_str = str(status) if status is not None else ""
                    code_str = str(code) if code is not None else ""
                    is_429 = status_str == "429" or code_str == "429"
                    status_int = None
                    try:
                        status_int = int(status_str)
                    except Exception:
                        try:
                            status_int = int(code_str)
                        except Exception:
                            status_int = None
                    transient = status_int in (500, 502, 503, 504)

                    if is_429:
                        delay = self._compute_retry_delay(exc, attempt, honor_retry_after=True)
                        logger.warning(
                            "Embedding throttled (429). attempt=%s batch_size=%s sleeping=%.2fs",
                            attempt,
                            batch_size,
                            delay,
                        )
                        time.sleep(delay)
                        if attempt >= 2 and batch_size > _EMBED_MIN_BATCH:
                            batch_size = max(_EMBED_MIN_BATCH, batch_size // 2)
                            logger.warning("Reducing embedding batch_size to %s due to repeated 429", batch_size)
                        if attempt <= _EMBED_MAX_RETRIES:
                            continue
                        logger.error(
                            "Embedding failed after retries (429). Skipping this batch of %s items.",
                            span,
                        )
                        break

                    if transient and attempt <= _EMBED_MAX_RETRIES:
                        delay = self._compute_retry_delay(exc, attempt, honor_retry_after=False)
                        logger.warning(
                            "Transient embedding error (status=%s). attempt=%s sleeping=%.2fs",
                            status,
                            attempt,
                            delay,
                        )
                        time.sleep(delay)
                        continue

                    logger.exception("Embedding error not recoverable; skipping batch of %s items", span)
                    break

            idx += span

        return flat_vecs, vec_map

    def _build_embed_payload(self, inputs: List[str], input_type: str | None, serving_mode):
        kwargs = {
            "serving_mode": serving_mode,
            "compartment_id": self._compartment_id,
            "inputs": inputs,
        }
        if "truncate" in self._details_init_params:
            kwargs["truncate"] = "END"
        if "input_type" in self._details_init_params and input_type:
            kwargs["input_type"] = input_type
        return self._models.EmbedTextDetails(**kwargs)

    def _extract_vectors(self, response) -> List[List[float]]:
        try:
            return list(getattr(response.data, "embeddings", []) or [])
        except Exception:  # noqa: BLE001
            return []

    def _compute_retry_delay(self, exc: Exception, attempt: int, *, honor_retry_after: bool) -> float:
        if honor_retry_after:
            retry_after = None
            headers = getattr(exc, "headers", None)
            if isinstance(headers, dict):
                retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after is None:
                response = getattr(exc, "response", None)
                if response is not None:
                    resp_headers = getattr(response, "headers", None)
                    if isinstance(resp_headers, dict):
                        retry_after = resp_headers.get("retry-after") or resp_headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                    if delay > 0:
                        return delay
                except Exception:  # noqa: BLE001
                    pass
        base = min(8.0, float(2 ** min(attempt, 5)))
        return base * random.uniform(0.5, 1.0)

    def _repair_bad_index_with_map(self, inputs: List[str], idx_map: List[int], bad_idx: int) -> Tuple[List[str], List[int]]:
        if bad_idx < 0 or bad_idx >= len(inputs):
            return inputs, idx_map
        text = inputs[bad_idx]
        action = self._on_token_limit
        if action == "split":
            parts = self._split_text_to_token_budget(text, self._max_input_tokens)
            if not parts:
                # remove item
                self.skipped_token_limit += 1
                return inputs[:bad_idx] + inputs[bad_idx + 1 :], idx_map[:bad_idx] + idx_map[bad_idx + 1 :]
            self.token_limit_splits += len(parts)
            new_inputs = inputs[:bad_idx] + parts + inputs[bad_idx + 1 :]
            new_map = idx_map[:bad_idx] + [idx_map[bad_idx]] * len(parts) + idx_map[bad_idx + 1 :]
            return new_inputs, new_map
        if action == "truncate":
            trimmed = self._truncate_to_budget(text, self._max_input_tokens)
            return inputs[:bad_idx] + [trimmed] + inputs[bad_idx + 1 :], idx_map
        # skip
        self.skipped_token_limit += 1
        return inputs[:bad_idx] + inputs[bad_idx + 1 :], idx_map[:bad_idx] + idx_map[bad_idx + 1 :]

    def _reassemble_by_map(self, flat_vectors: List[List[float]], vec_map: List[int], original_len: int) -> List[List[float]]:
        # Accumulate sums per original index
        sums: Dict[int, List[float]] = {}
        counts: Dict[int, int] = {}
        for v, orig_idx in zip(flat_vectors, vec_map):
            if not isinstance(v, list) or not v:
                continue
            if orig_idx not in sums:
                sums[orig_idx] = list(v)
                counts[orig_idx] = 1
            else:
                acc = sums[orig_idx]
                for i in range(len(acc)):
                    acc[i] += v[i]
                counts[orig_idx] += 1
        # Build result list
        out: List[List[float]] = []
        for idx in range(original_len):
            if idx in sums and counts.get(idx, 0) > 0:
                c = counts[idx]
                acc = sums[idx]
                out.append([x / c for x in acc])
            else:
                out.append([])
        return out
