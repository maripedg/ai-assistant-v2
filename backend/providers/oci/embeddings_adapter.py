# backend/providers/oci/embeddings_adapter.py
from __future__ import annotations

import inspect
import os
from typing import List, Iterable, Tuple, Dict, Any
import logging
import math
import re
import pathlib

logger = logging.getLogger(__name__)
from langchain_core.embeddings import Embeddings
import oci

try:
    # Cliente real de LangChain para OCI GenAI
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

    # Métodos esperados por LangChain:

    def embed_documents(self, texts: List[str], input_type: str | None = None) -> List[List[float]]:
        # Determine serving mode
        if self._model_id.startswith("ocid1.generativeaiendpoint"):
            serving_mode = self._models.DedicatedServingMode(endpoint_id=self._model_id)
        else:
            serving_mode = self._models.OnDemandServingMode(model_id=self._model_id)

        embeddings: List[List[float]] = []
        batch_size = 96
        for i in range(0, len(texts), batch_size):
            original_batch = texts[i : i + batch_size]
            # Preflight transform each text to fit within token budget
            expanded, exp_map = self._preflight_expand_batch(original_batch)
            # Perform provider call with retry on token-limit 400s (tracking mapping)
            flat_vecs, out_map = self._embed_with_retry(serving_mode, expanded, input_type or self._doc_input_type, exp_map)
            # Reassemble per original position (average when split, empty vector when skipped)
            batch_vecs = self._reassemble_by_map(flat_vecs, out_map, len(original_batch))
            embeddings.extend(batch_vecs)
        return embeddings

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
        attempt = 0
        expanded = list(inputs)
        idx_map = list(exp_map)
        while attempt < 3:
            attempt += 1
            try:
                kwargs = {
                    "serving_mode": serving_mode,
                    "compartment_id": self._compartment_id,
                    "inputs": expanded,
                }
                if "truncate" in self._details_init_params:
                    kwargs["truncate"] = "END"
                if "input_type" in self._details_init_params and input_type:
                    kwargs["input_type"] = input_type
                details = self._models.EmbedTextDetails(**kwargs)
                resp = self._client.embed_text(details)
                return resp.data.embeddings, list(idx_map)
            except Exception as exc:  # noqa: BLE001
                # Detect OCI 400 token-limit style errors
                msg = str(exc)
                idx_match = re.search(r"texts\[(\d+)\] is too long", msg)
                status_400 = ("400" in msg) or (getattr(exc, "status", None) == 400)
                if status_400 and idx_match:
                    self.errors_token_limit += 1
                    bad_idx = int(idx_match.group(1))
                    logger.warning("Provider 400 on inputs[%d]; applying token-limit strategy and retry (attempt %d)", bad_idx, attempt)
                    expanded, idx_map = self._repair_bad_index_with_map(expanded, idx_map, bad_idx)
                    continue
                if attempt >= 3:
                    break
                raise
        # Last-resort: skip any remaining offenders by removing too-long items
        survivors: List[str] = []
        survivors_map: List[int] = []
        for j, s in enumerate(expanded):
            if self._estimate_tokens(s) <= self._max_input_tokens:
                survivors.append(s)
                survivors_map.append(idx_map[j])
            else:
                self.skipped_token_limit += 1
                logger.warning("Token limit: forced skip inputs[%d] after retries (~%d tokens)", j, self._estimate_tokens(s))
        if not survivors:
            return [], []
        # Attempt final call; if provider still flags an index, skip offenders and retry until success
        while True:
            if not survivors:
                return [], []
            kwargs = {
                "serving_mode": serving_mode,
                "compartment_id": self._compartment_id,
                "inputs": survivors,
            }
            if "truncate" in self._details_init_params:
                kwargs["truncate"] = "END"
            if "input_type" in self._details_init_params and input_type:
                kwargs["input_type"] = input_type
            details = self._models.EmbedTextDetails(**kwargs)
            try:
                resp = self._client.embed_text(details)
                return resp.data.embeddings, list(survivors_map)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                idx_match = re.search(r"texts\[(\d+)\] is too long", msg)
                status_400 = ("400" in msg) or (getattr(exc, "status", None) == 400)
                if status_400 and idx_match:
                    self.errors_token_limit += 1
                    bad_idx = int(idx_match.group(1))
                    if 0 <= bad_idx < len(survivors):
                        logger.warning("Token limit: forced skip inputs[%d] after retries (post-filter)", bad_idx)
                        self.skipped_token_limit += 1
                        survivors.pop(bad_idx)
                        survivors_map.pop(bad_idx)
                        continue
                raise

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
