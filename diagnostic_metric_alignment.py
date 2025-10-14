#!/usr/bin/env python3
"""
diagnostic_metric_alignment.py

Read-only diagnostic to verify embedding metric alignment with retrieval
configuration and normalization expectations. Prints a clear PASS/FAIL summary.

Constraints:
- Does not modify any files, DBs, indices, aliases, or environment variables.
- Uses only stdlib + requests if available.

Defaults are set for a typical project layout, but flags allow overrides.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Optional third-party dependency: requests (allowed by spec). Fallback to None if missing.
try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore


# Optional third-party dependency: yaml (PyYAML). If missing, we degrade gracefully.
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def load_yaml_file(path: str) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if yaml is None:
            eprint(f"WARN: PyYAML not available; cannot parse {path}.")
            return None
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data
        eprint(f"WARN: YAML root in {path} is not a mapping; got {type(data).__name__}.")
        return None
    except Exception as ex:
        eprint(f"ERROR: Failed to load YAML {path}: {ex}")
        return None


def coalesce_paths(primary: str, fallbacks: Iterable[str]) -> str:
    # Prefer primary if exists, else the first existing fallback, else return primary (likely missing)
    if primary and os.path.exists(primary):
        return primary
    for fb in fallbacks:
        if fb and os.path.exists(fb):
            return fb
    return primary


def canonicalize_metric(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    # Common synonyms
    if v in {"dot", "dotproduct", "inner", "inner_product", "ip"}:
        return "dot_product"
    if v in {"cos", "cosine_similarity", "cos_sim"}:
        return "cosine"
    if v in {"dot_product", "cosine"}:
        return v
    # Unknown metric string; return as-is for visibility
    return v


def canonicalize_score_mode(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = str(value).strip().lower().replace("-", "_")
    if v in {"normalized", "norm", "unit", "unit_norm"}:
        return "normalized"
    if v in {"raw", "unnormalized"}:
        return "raw"
    return v


def deep_get_first_metric_block(d: Any) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """Attempt to find a block that declares retrieval metric and score_mode.
    Returns (distance, score_mode, extras) where extras may include thresholds.
    """
    found_distance: Optional[str] = None
    found_score_mode: Optional[str] = None
    extras: Dict[str, Any] = {}

    def visit(node: Any) -> None:
        nonlocal found_distance, found_score_mode, extras
        if isinstance(node, dict):
            # Consider node itself
            cand_dist = node.get("distance") or node.get("metric") or node.get("similarity")
            cand_score = node.get("score_mode") or node.get("scoring")
            if cand_dist and (found_distance is None):
                found_distance = canonicalize_metric(cand_dist)
            if cand_score and (found_score_mode is None):
                found_score_mode = canonicalize_score_mode(cand_score)

            # Thresholds
            for key in ("threshold", "min_score", "similarity_threshold", "score_threshold"):
                if key in node and key not in extras:
                    extras[key] = node.get(key)

            # Early exit if both found
            if found_distance and found_score_mode:
                return

            # Heuristic: look into plausible subkeys first
            for k in (
                "retrieval",
                "search",
                "vector",
                "semantic",
                "dense",
                "rag",
                "index",
                "store",
                "similarity",
                "ann",
                "faiss",
                "opensearch",
                "elasticsearch",
            ):
                if k in node:
                    visit(node[k])
                    if found_distance and found_score_mode:
                        return

            # Generic traversal
            for v in node.values():
                visit(v)
                if found_distance and found_score_mode:
                    return
        elif isinstance(node, list):
            for v in node:
                visit(v)
                if found_distance and found_score_mode:
                    return

    try:
        visit(d)
    except Exception:
        pass
    return found_distance, found_score_mode, extras


def parse_embedding_profile(cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[bool], str]:
    """Parse embedding config. Return (metric, normalized, profile_used).
    Heuristics:
      - If profiles exist, prefer profiles.active or default, else 'profiles.default'.
      - Look for keys: metric|distance, normalize|l2_normalize|unit_norm|normalized
    """
    profile_used = "profiles.default"

    profiles = None
    if isinstance(cfg, dict):
        profiles = cfg.get("profiles") or cfg.get("embedding_profiles") or cfg.get("embeddings")

    selected: Optional[Dict[str, Any]] = None

    def extract_from_block(block: Dict[str, Any]) -> Tuple[Optional[str], Optional[bool]]:
        metric = canonicalize_metric(
            block.get("metric")
            or block.get("distance")
            or block.get("similarity")
        )
        norm_val = (
            block.get("normalize")
            or block.get("l2_normalize")
            or block.get("unit_norm")
            or block.get("normalized")
            or block.get("pre_normalize")
        )
        if isinstance(norm_val, str):
            nv = norm_val.strip().lower()
            if nv in {"true", "yes", "1"}:
                normalized = True
            elif nv in {"false", "no", "0"}:
                normalized = False
            else:
                normalized = None
        elif isinstance(norm_val, bool):
            normalized = norm_val
        else:
            normalized = None
        return metric, normalized

    if isinstance(profiles, dict):
        # If an explicit active profile is indicated
        active_key = (
            cfg.get("active_profile")
            or cfg.get("default_profile")
            or (profiles.get("active") if isinstance(profiles.get("active"), str) else None)
        )
        candidate: Optional[Dict[str, Any]] = None
        if active_key and isinstance(active_key, str):
            candidate = profiles.get(active_key) if isinstance(profiles.get(active_key), dict) else None
            if candidate is not None:
                selected = candidate
                profile_used = f"profiles.{active_key}"
        if selected is None:
            # Fallback to 'default' in profiles
            default_block = profiles.get("default") if isinstance(profiles.get("default"), dict) else None
            if default_block is not None:
                selected = default_block
                profile_used = "profiles.default"
        if selected is None:
            # Otherwise, pick the first mapping
            for k, v in profiles.items():
                if isinstance(v, dict):
                    selected = v
                    profile_used = f"profiles.{k}"
                    break

    # If no profiles mapping, treat top-level as a profile-like block
    if selected is None and isinstance(cfg, dict):
        selected = cfg
        profile_used = "root"

    metric, normalized = extract_from_block(selected or {})
    return metric, normalized, profile_used


def deterministic_dummy_embedding(text: str, dim: int = 768) -> List[float]:
    # Deterministic pseudo-random vector derived from text and index
    vec: List[float] = []
    base = text or ""
    for i in range(dim):
        h = hashlib.sha256(f"{base}|{i}".encode("utf-8")).digest()
        # Take first 8 bytes as integer, map to [-0.5, 0.5]
        val = int.from_bytes(h[:8], byteorder="big", signed=False)
        # Normalize to [0,1)
        u = (val % (10**12)) / float(10**12)
        vec.append(u - 0.5)
    return vec


def dot_product(a: List[float], b: List[float]) -> float:
    return sum((x * y) for x, y in zip(a, b))


def l2_norm(a: List[float]) -> float:
    return math.sqrt(sum((x * x) for x in a))


def cosine_similarity(a: List[float], b: List[float]) -> float:
    na = l2_norm(a)
    nb = l2_norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot_product(a, b) / (na * nb)


def try_parse_vector_from_json(obj: Any) -> Optional[List[float]]:
    # Accept raw list
    if isinstance(obj, list) and all(isinstance(x, (int, float)) for x in obj):
        return [float(x) for x in obj]
    if isinstance(obj, dict):
        # Common patterns
        for key in ("embedding", "vector", "values"):
            val = obj.get(key)
            if isinstance(val, list) and all(isinstance(x, (int, float)) for x in val):
                return [float(x) for x in val]
        # OpenAI-style { data: [{ embedding: [...] }] }
        data = obj.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                emb = first.get("embedding")
                if isinstance(emb, list) and all(isinstance(x, (int, float)) for x in emb):
                    return [float(x) for x in emb]
        # Cohere/other providers sometimes return { embeddings: [[...]] }
        embs = obj.get("embeddings")
        if isinstance(embs, list) and embs and isinstance(embs[0], list):
            if all(isinstance(x, (int, float)) for x in embs[0]):
                return [float(x) for x in embs[0]]
    return None


def fetch_embedding(
    endpoint: str,
    text: str,
    model: Optional[str] = None,
    timeout: int = 10,
) -> Optional[List[float]]:
    if requests is None:
        eprint("WARN: requests not available; cannot call embed endpoint.")
        return None
    payload_options = []
    # Try a few common payload shapes
    if model:
        payload_options.append({"input": text, "model": model})  # OpenAI style
        payload_options.append({"text": text, "model": model})
        payload_options.append({"content": text, "model": model})
    payload_options.extend([
        {"input": text},
        {"text": text},
        {"content": text},
        {"query": text},
    ])

    headers = {"Content-Type": "application/json"}
    last_error: Optional[str] = None
    for payload in payload_options:
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                continue
            try:
                obj = resp.json()
            except Exception:
                last_error = "Non-JSON response"
                continue
            vec = try_parse_vector_from_json(obj)
            if vec is not None:
                return vec
            last_error = "No vector found in response"
        except Exception as ex:
            last_error = str(ex)
    if last_error:
        eprint(f"WARN: Failed to fetch embedding from {endpoint}: {last_error}")
    return None


def recursive_find_keys(obj: Any, wanted: Iterable[str]) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    want = set(wanted)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in want and k not in found:
                    found[k] = v
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    try:
        visit(obj)
    except Exception:
        pass
    return found


def call_chat_for_diagnostics(endpoint: str, question: str, timeout: int = 10) -> Dict[str, Any]:
    result: Dict[str, Any] = {"_ok": False}
    if requests is None:
        eprint("WARN: requests not available; cannot call chat endpoint.")
        return result

    headers = {"Content-Type": "application/json"}
    payload_candidates = [
        {"question": question},
        {"query": question},
        {"input": question},
        {"message": question},
        {"prompt": question},
    ]

    last_error = None
    for payload in payload_candidates:
        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                continue
            try:
                obj = resp.json()
            except Exception:
                last_error = "Non-JSON response"
                continue
            # Try to find decision_explain fields anywhere in the structure
            info = recursive_find_keys(obj, [
                "decision_explain",
                "distance",
                "score_mode",
                "max_similarity",
                "raw_score",
                "similarity",
            ])

            decision = info.get("decision_explain")
            if isinstance(decision, dict):
                result.update(decision)
                # Promote common expected fields if present
                for k in ("distance", "score_mode", "max_similarity"):
                    if k in decision:
                        result[k] = decision[k]
            else:
                # If not nested, directly use found keys
                if "distance" in info:
                    result["distance"] = info["distance"]
                if "score_mode" in info:
                    result["score_mode"] = info["score_mode"]
                if "max_similarity" in info:
                    result["max_similarity"] = info["max_similarity"]

            # Attempt to extract top item score/similarity if possible
            # Heuristic scan for fields named raw_score/similarity in lists
            def scan_for_top(obj: Any) -> Optional[Tuple[Optional[float], Optional[float]]]:
                best_raw = None
                best_sim = None
                if isinstance(obj, dict):
                    if "raw_score" in obj or "similarity" in obj:
                        try:
                            if "raw_score" in obj:
                                best_raw = float(obj.get("raw_score"))
                            if "similarity" in obj:
                                best_sim = float(obj.get("similarity"))
                            return best_raw, best_sim
                        except Exception:
                            pass
                    for v in obj.values():
                        found = scan_for_top(v)
                        if found is not None:
                            return found
                elif isinstance(obj, list):
                    for it in obj:
                        found = scan_for_top(it)
                        if found is not None:
                            return found
                return None

            top = scan_for_top(obj)
            if top is not None:
                raw, sim = top
                if raw is not None:
                    result["top_raw_score"] = raw
                if sim is not None:
                    result["top_similarity"] = sim

            result["_ok"] = True
            return result
        except Exception as ex:
            last_error = str(ex)
            continue

    if last_error:
        eprint(f"WARN: Failed to query chat endpoint {endpoint}: {last_error}")
    return result


def decide_alignment(
    retrieval_metric: Optional[str],
    retrieval_score_mode: Optional[str],
    embed_metric: Optional[str],
    embed_normalized: Optional[bool],
) -> Tuple[bool, List[str]]:
    tips: List[str] = []

    # If any unknown key info, we still attempt a decision but will likely be False with guidance.
    metrics_aligned = (retrieval_metric is not None and embed_metric is not None and retrieval_metric == embed_metric)
    if not metrics_aligned:
        if retrieval_metric and embed_metric and retrieval_metric != embed_metric:
            tips.append(
                f"Set retrieval.distance={embed_metric} to match embed.metric, or re-embed to {retrieval_metric}."
            )
        else:
            tips.append("Ensure retrieval.distance and embedding metric are explicitly set and consistent.")

    # Normalization consistency
    norm_consistent = True
    if retrieval_metric == "dot_product":
        if retrieval_score_mode == "normalized":
            # Expect embeddings L2-normalized at creation or an explicit normalization mapping at query time.
            if embed_normalized is False:
                norm_consistent = False
                tips.append(
                    "Vectors are not L2-normalized but score_mode=normalized. Normalize embeddings or switch score_mode to raw."
                )
            elif embed_normalized is None:
                # Unknown; be conservative
                norm_consistent = False
                tips.append(
                    "Embedding normalization unknown while score_mode=normalized. Confirm L2-normalization or switch to cosine."
                )
        else:
            # raw dot-product is generally fine regardless of pre-normalization
            norm_consistent = True
    else:
        # cosine similarity inherently normalizes during scoring; pre-normalization optional
        norm_consistent = True

    aligned = metrics_aligned and norm_consistent
    return aligned, tips


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify alignment between embedding metric and retrieval settings. Read-only.",
    )

    parser.add_argument(
        "--app-config",
        default="/app/backend/config/app.yaml",
        help="Path to application config (retrieval settings). Default: /app/backend/config/app.yaml",
    )
    parser.add_argument(
        "--embed-config",
        default="/app/backend/config/embeddings.yaml",
        help="Path to embeddings config. Default: /app/backend/config/embeddings.yaml",
    )
    parser.add_argument(
        "--question",
        default="",
        help="Test query text (optional).",
    )
    parser.add_argument(
        "--chunk-text",
        default="",
        help="Reference chunk text (optional).",
    )
    parser.add_argument(
        "--chat-endpoint",
        default=None,
        help="Chat URL to trigger retrieval (optional). Example: http://localhost:8000/chat",
    )
    parser.add_argument(
        "--embed-endpoint",
        default=None,
        help="Embedding service URL (optional). If provided, POST to fetch embeddings.",
    )
    parser.add_argument(
        "--embed-model",
        default=None,
        help="Embedding model id/name for the embed endpoint (optional).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP timeout seconds for endpoints. Default: 10",
    )

    args = parser.parse_args(argv)

    # Resolve paths with sensible local fallbacks if Docker-style defaults don't exist
    app_config_path = coalesce_paths(
        args.app_config,
        (
            os.path.join("backend", "config", "app.yaml"),
            os.path.join("/workspace", "backend", "config", "app.yaml"),
        ),
    )
    embed_config_path = coalesce_paths(
        args.embed_config,
        (
            os.path.join("backend", "config", "embeddings.yaml"),
            os.path.join("/workspace", "backend", "config", "embeddings.yaml"),
        ),
    )

    print("Loading configs...")
    app_cfg = load_yaml_file(app_config_path)
    if app_cfg is None:
        eprint(f"WARN: Could not load app config at {app_config_path}. Retrieval settings will be 'unknown'.")
    embed_cfg = load_yaml_file(embed_config_path)
    if embed_cfg is None:
        eprint(f"WARN: Could not load embedding config at {embed_config_path}. Embedding settings will be 'unknown'.")

    # Parse retrieval
    retrieval_distance: Optional[str] = None
    retrieval_score_mode: Optional[str] = None
    thresholds: Dict[str, Any] = {}
    if isinstance(app_cfg, dict):
        rd, sm, extras = deep_get_first_metric_block(app_cfg)
        retrieval_distance = rd
        retrieval_score_mode = sm
        thresholds = extras or {}

    print("Retrieval settings from app config:")
    print(f"- distance: {retrieval_distance if retrieval_distance is not None else 'unknown'}")
    print(f"- score_mode: {retrieval_score_mode if retrieval_score_mode is not None else 'unknown'}")
    if thresholds:
        for k, v in thresholds.items():
            print(f"- {k}: {v}")

    # Parse embedding profile
    embed_metric: Optional[str] = None
    embed_normalized: Optional[bool] = None
    profile_used = "unknown"
    if isinstance(embed_cfg, dict):
        embed_metric, embed_normalized, profile_used = parse_embedding_profile(embed_cfg)

    print("Embedding settings from embeddings config:")
    print(f"- profile: {profile_used}")
    print(f"- metric: {embed_metric if embed_metric is not None else 'unknown'}")
    print(f"- normalized: {embed_normalized if embed_normalized is not None else 'unknown'}")

    # Obtain embeddings (endpoint or fallback)
    q_text = args.question if args.question else "Question placeholder"
    c_text = args.chunk_text if args.chunk_text else "Chunk placeholder"

    q_vec: Optional[List[float]] = None
    c_vec: Optional[List[float]] = None

    if args.embed_endpoint:
        print(f"Fetching embeddings from {args.embed_endpoint} (timeout={args.timeout})...")
        q_vec = fetch_embedding(args.embed_endpoint, q_text, model=args.embed_model, timeout=args.timeout)
        c_vec = fetch_embedding(args.embed_endpoint, c_text, model=args.embed_model, timeout=args.timeout)

    if q_vec is None or c_vec is None:
        print("Using deterministic synthetic embeddings for structural checks (no external calls).")
        q_vec = deterministic_dummy_embedding(q_text)
        c_vec = deterministic_dummy_embedding(c_text)

    # Compute similarities
    dp = dot_product(q_vec, c_vec)  # type: ignore[arg-type]
    cs = cosine_similarity(q_vec, c_vec)  # type: ignore[arg-type]
    print("Local similarity checks:")
    print(f"- dot_product: {dp:.6f}")
    print(f"- cosine: {cs:.6f}")
    dp_norm_proxy: Optional[float] = None
    if retrieval_score_mode == "normalized" and retrieval_distance == "dot_product":
        dp_norm_proxy = (dp + 1.0) / 2.0
        print(f"- dot_product_normalized_proxy: {dp_norm_proxy:.6f}")

    # Optional runtime cross-check via chat
    chat_info: Dict[str, Any] = {}
    if args.chat_endpoint and args.question:
        print(f"Querying chat endpoint for diagnostics: {args.chat_endpoint}")
        chat_info = call_chat_for_diagnostics(args.chat_endpoint, args.question, timeout=args.timeout)
        if chat_info.get("_ok"):
            dist = canonicalize_metric(str(chat_info.get("distance")) if chat_info.get("distance") is not None else None)
            sm = canonicalize_score_mode(str(chat_info.get("score_mode")) if chat_info.get("score_mode") is not None else None)
            print("Runtime decision_explain (from chat):")
            if dist:
                print(f"- distance: {dist}")
            if sm:
                print(f"- score_mode: {sm}")
            if "max_similarity" in chat_info:
                print(f"- max_similarity: {chat_info.get('max_similarity')}")
            if "top_raw_score" in chat_info or "top_similarity" in chat_info:
                raw = chat_info.get("top_raw_score")
                sim = chat_info.get("top_similarity")
                if raw is not None:
                    print(f"- top_raw_score: {raw}")
                if sim is not None:
                    print(f"- top_similarity: {sim}")
        else:
            eprint("WARN: Chat endpoint did not return usable diagnostics.")

    # Alignment decision
    aligned, tips = decide_alignment(retrieval_distance, retrieval_score_mode, embed_metric, embed_normalized)

    # Summary block (STRICT: single summary block at end)
    print("==== SUMMARY ====")
    print(f"retrieval.distance: {retrieval_distance if retrieval_distance is not None else 'unknown'}")
    print(f"retrieval.score_mode: {retrieval_score_mode if retrieval_score_mode is not None else 'unknown'}")
    print(f"embed.metric: {embed_metric if embed_metric is not None else 'unknown'}")
    print(f"embed.normalized: {embed_normalized if embed_normalized is not None else 'unknown'}")
    print(f"dot_product: {dp:.6f}")
    print(f"cosine: {cs:.6f}")
    if dp_norm_proxy is not None:
        print(f"dot_product_normalized_proxy: {dp_norm_proxy:.6f}")
    # Runtime chat echo if present
    if chat_info.get("_ok"):
        dist = canonicalize_metric(str(chat_info.get("distance")) if chat_info.get("distance") is not None else None)
        sm = canonicalize_score_mode(str(chat_info.get("score_mode")) if chat_info.get("score_mode") is not None else None)
        print(f"chat.distance: {dist if dist is not None else 'unknown'}")
        print(f"chat.score_mode: {sm if sm is not None else 'unknown'}")
    print(f"ALIGNED: {'true' if aligned else 'false'}")
    if tips:
        # Join tips into a single line each for clarity; spec allows human-readable logs
        for t in tips:
            print(f"tip: {t}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

