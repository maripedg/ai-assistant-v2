from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

import streamlit as st
from streamlit_chat import message

from app_config.env import get_config

CONFIG = get_config()
DEBUG_CHAT_UI_STRICT = bool(CONFIG.get("DEBUG_CHAT_UI_STRICT", False))
DEBUG_CHAT_UI = bool(CONFIG.get("DEBUG_CHAT_UI", False)) or DEBUG_CHAT_UI_STRICT
LOGGER = logging.getLogger("chat_ui")
if DEBUG_CHAT_UI:
    LOGGER.setLevel(logging.DEBUG)


def logp(msg: str, **kwargs: Any) -> None:
    if not DEBUG_CHAT_UI:
        return
    try:
        payload = json.dumps(kwargs, default=str)
    except Exception:  # noqa: BLE001
        payload = str(kwargs)
    try:
        LOGGER.debug("UI:%s | %s", msg, payload)
    except Exception:  # noqa: BLE001
        print(f"UI:{msg} | {payload}")

MODE_DETAILS = {
    "rag": {
        "icon": "📚",
        "label": "RAG",
        "color": "#2a6fdf",
        "tooltip": "Answer grounded on your documents.",
    },
    "hybrid": {
        "icon": "🧭",
        "label": "Hybrid",
        "color": "#7a3bdc",
        "tooltip": "Combined documents + model judgment.",
    },
    "fallback": {
        "icon": "🛟",
        "label": "Fallback",
        "color": "#a37c00",
        "tooltip": "No sufficient evidence; controlled backup answer.",
    },
    "direct": {
        "icon": "⚡",
        "label": "Direct",
        "color": "#2c9b5d",
        "tooltip": "Answered without retrieval.",
    },
    "unknown": {
        "icon": "❓",
        "label": "Unknown",
        "color": "#666666",
        "tooltip": "Answer confidence unavailable.",
    },
}

ANSWER_FIELD_ORDER = ("answer", "answer2", "answer3")
ANSWER_PLACEHOLDER = "No answer content returned."


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_confidence(decision: Dict[str, Any], mode: str) -> Dict[str, Optional[float]]:
    sim_max = _safe_float(decision.get("max_similarity"))
    thr_low = _safe_float(decision.get("threshold_low"))
    thr_high = _safe_float(decision.get("threshold_high"))

    if mode == "fallback":
        return {"label": "Low", "ratio": 0.0, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}

    if sim_max is None or thr_low is None or thr_high is None or thr_high <= thr_low:
        if sim_max is None or thr_low is None:
            return {"label": "Unknown", "ratio": None, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}
        label = "High" if sim_max >= thr_low else "Low"
        return {"label": label, "ratio": None, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}

    if sim_max >= thr_high:
        label = "High"
    elif sim_max >= thr_low:
        label = "Medium"
    else:
        label = "Low"

    ratio = (sim_max - thr_low) / (thr_high - thr_low)
    ratio = max(0.0, min(1.0, ratio))
    return {"label": label, "ratio": ratio, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}


def _should_show_sources(mode: str, decision: Dict[str, Any], sim_max: Optional[float], thr_low: Optional[float]) -> bool:
    if mode == "fallback":
        return False
    gate_failed = decision.get("gate_failed")
    if isinstance(gate_failed, str):
        gate_failed = gate_failed.lower() == "true"
    if gate_failed:
        return False
    if sim_max is not None and thr_low is not None and sim_max < thr_low:
        return False
    return True


def _filter_evidence_chunks(chunks: List[Dict[str, Any]], decision: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    sim_max = _safe_float(decision.get("max_similarity"))
    thr_low = _safe_float(decision.get("threshold_low"))
    show_sources = _should_show_sources(mode, decision, sim_max, thr_low)
    if not show_sources:
        return []

    if thr_low is None:
        return list(chunks)

    filtered = []
    for chunk in chunks:
        score = _safe_float(chunk.get("score"))
        if score is None or score >= thr_low:
            filtered.append(chunk)
    return filtered


def _sanitize_snippet(snippet: str, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", snippet or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _format_chunk(chunk: Dict[str, Any]) -> Tuple[str, str, str, Optional[float]]:
    source = chunk.get("source") or ""
    file_name = Path(source).name or (chunk.get("document_id") or "document")
    doc_ref = chunk.get("doc_id") or chunk.get("chunk_id") or "n/a"
    snippet = _sanitize_snippet(chunk.get("snippet") or chunk.get("text") or "")
    score = _safe_float(chunk.get("score"))
    return file_name, doc_ref, snippet, score


def _select_answer_text(payload: Dict[str, Any]) -> Tuple[Optional[str], str]:
    for field in ANSWER_FIELD_ORDER:
        value = payload.get(field)
        if isinstance(value, str):
            if value.strip():
                return value, field
        elif value is not None:
            text = str(value)
            if text.strip():
                return text, field
    return None, "none"


def render_primary_answer(payload: Dict[str, Any]) -> Tuple[str, str]:
    from textwrap import shorten

    answer_value: Optional[str] = None
    source_key = "none"
    for key in ANSWER_FIELD_ORDER:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            answer_value = candidate
            source_key = key
            break
        if candidate is not None:
            as_text = str(candidate).strip()
            if as_text:
                answer_value = as_text
                source_key = key
                break

    logp("render_primary_answer:start", source=source_key, len_answer=len(answer_value or ""))

    if not answer_value:
        st.info(ANSWER_PLACEHOLDER)
        logp("render_primary_answer:none")
        return "", "none"

    normalized = answer_value.replace("\r\n", "\n").replace("\r", "\n")

    if DEBUG_CHAT_UI_STRICT:
        st.markdown("<style>.stMarkdown{outline:1px dotted #915 !important;}</style>", unsafe_allow_html=True)
        with st.container():
            st.markdown(
                f"""
<div style="border:2px dashed #915; padding:12px; background:#fff7fb;">
  <strong>DEBUG — Answer Box (source: {source_key})</strong>
</div>
""",
                unsafe_allow_html=True,
            )
            st.caption(f"len(answer)={len(normalized)} | preview: {shorten(normalized, 120)}")
            st.code(normalized[:400], language="markdown")
            logp("render_primary_answer:box_shown", preview=normalized[:120])

    try:
        st.markdown(normalized)
        logp("render_primary_answer:markdown_rendered", ok=True, len_answer=len(normalized))
    except Exception as exc:  # noqa: BLE001
        logp("render_primary_answer:error", error=str(exc))
        st.error("Error rendering answer.")
        return "", source_key

    return normalized, source_key


def _render_mode_chip(mode_key: str) -> None:
    details = MODE_DETAILS.get(mode_key, MODE_DETAILS["unknown"])
    style = (
        "display:inline-flex;align-items:center;gap:0.4rem;"
        f"padding:0.1rem 0.6rem;border-radius:999px;border:1px solid {details['color']};"
        f"color:{details['color']};font-size:0.85rem;font-weight:600;"
    )
    st.markdown(
        f"<span style='{style}'>{details['icon']} {details['label']}</span>",
        unsafe_allow_html=True,
    )
    st.caption(details["tooltip"])


def _render_evidence_list(chunks: List[Dict[str, Any]], sources_used: Optional[str]) -> None:
    if not chunks:
        return
    expanded = bool(chunks)
    title = f"Sources ({len(chunks)})"
    with st.expander(title, expanded=expanded):
        if sources_used == "partial":
            st.info("Used a subset of available context.")
        for idx, chunk in enumerate(chunks, start=1):
            file_name, doc_ref, snippet, score = _format_chunk(chunk)
            st.markdown(f"**{idx}. {file_name}** (doc: `{doc_ref}`)")
            if score is not None:
                st.caption(f"Relevance: {score:.2f}")
            st.markdown(snippet or "_(no preview available)_")
            st.markdown("---")


def _render_mode_explanation(
    mode: str,
    decision: Dict[str, Any],
    sim_max: Optional[float],
    thr_low: Optional[float],
    thr_high: Optional[float],
    show_allowed: bool,
    has_sources: bool,
) -> None:
    explanations = {
        "rag": "We found sufficient evidence (max similarity {sim} ≥ {thr}) and grounded the answer on retrieved documents.",
        "hybrid": "We combined retrieved documents and model judgment because evidence met the threshold (max similarity {sim} ≥ {thr}).",
        "fallback": "No evidence met the quality bar (max similarity {sim} < {thr}); we returned a safe backup answer.",
        "direct": "No retrieval was required. The model answered directly.",
    }
    fallback_message = "No sources displayed because they didn’t meet the quality threshold."

    with st.expander("Why this mode?", expanded=False):
        sim_txt = f"{sim_max:.2f}" if sim_max is not None else "n/a"
        thr_txt = f"{thr_high:.2f}" if mode == "rag" and thr_high is not None else f"{thr_low:.2f}" if thr_low is not None else "n/a"
        template = explanations.get(mode, "Answer strategy details unavailable.")
        st.write(template.format(sim=sim_txt, thr=thr_txt))

        confidence = _compute_confidence(decision, mode)
        ratio = confidence.get("ratio")
        label = confidence.get("label", "Unknown")
        if ratio is not None:
            st.progress(ratio, text=f"Confidence: {label}")
        else:
            st.caption(f"Confidence: {label}")

        if not show_allowed:
            st.info(fallback_message)
        elif not has_sources:
            st.info("No sources displayed because they didn’t meet the quality threshold.")


def _render_decision_details(meta: Dict[str, Any]) -> None:
    mode_key = (meta.get("mode") or "unknown").lower()
    _render_mode_chip(mode_key)

    decision = meta.get("decision_explain") or {}
    confidence = _compute_confidence(decision, mode_key)
    sim_max = confidence.get("sim_max")
    thr_low = confidence.get("threshold_low")
    thr_high = confidence.get("threshold_high")
    kept_n = decision.get("kept_n")
    try:
        kept_n_value = int(kept_n)
    except (TypeError, ValueError):
        kept_n_value = 0
    summary_evidence = len(meta.get("used_chunks") or []) or kept_n_value
    summary_line = f"Mode: {MODE_DETAILS.get(mode_key, MODE_DETAILS['unknown'])['label']}. Evidence: {summary_evidence}. Confidence: {confidence.get('label')}."
    st.markdown(summary_line)

    raw_chunks = meta.get("used_chunks") or []
    filtered_chunks = _filter_evidence_chunks(raw_chunks, decision, mode_key)
    show_allowed = _should_show_sources(mode_key, decision, sim_max, thr_low)
    if filtered_chunks:
        _render_evidence_list(filtered_chunks, meta.get("sources_used"))
    elif show_allowed and raw_chunks:
        st.caption("No sources met the display threshold.")

    _render_mode_explanation(
        mode_key,
        decision,
        sim_max,
        thr_low,
        thr_high,
        show_allowed,
        bool(filtered_chunks),
    )


def _build_answer_meta(result: Dict[str, Any], answer_text: Optional[str], answer_field: str) -> Dict[str, Any]:
    clean_text = (answer_text or "").strip()
    return {
        "mode": (result.get("mode") or "unknown").lower(),
        "used_chunks": result.get("used_chunks") or [],
        "sources_used": result.get("sources_used"),
        "decision_explain": result.get("decision_explain") or {},
        "answer_text": clean_text,
        "answer_field": answer_field,
        "has_answer": bool(clean_text),
        "raw_result": result,
    }


def _latest_user_question(history: List[Tuple[str, str]], current_index: int) -> str:
    for idx in range(current_index - 1, -1, -1):
        role, content = history[idx]
        if role == "user":
            return content
    return ""


def _render_feedback_controls(idx: int, content: str, meta: Optional[Dict[str, Any]], api_client) -> None:
    msg_id = f"{idx}_{abs(hash(content)) % 1_000_000}"
    already = st.session_state.get(f"fb_done_{msg_id}", False)
    state = st.session_state.feedback_mode.setdefault(idx, {"icon": None})

    cols = st.columns([1, 1, 6], gap="small")
    like_clicked = cols[0].button("\U0001F44D", key=f"like_{msg_id}", disabled=already)
    dislike_clicked = cols[1].button("\U0001F44E", key=f"dislike_{msg_id}", disabled=already)
    if like_clicked:
        state["icon"] = "like"
        state["needs_reset"] = True
        st.session_state.feedback_mode[idx] = state
    if dislike_clicked:
        state["icon"] = "dislike"
        state["needs_reset"] = True
        st.session_state.feedback_mode[idx] = state

    if already:
        with cols[2]:
            st.caption("Thanks for your feedback.")
        return

    if state.get("icon"):
        comment_key = f"feedback_comment_{msg_id}"
        if state.pop("needs_reset", None):
            st.session_state.pop(comment_key, None)
            st.session_state.feedback_mode[idx] = state

        with cols[2]:
            st.text_area("Add a note (optional)", key=comment_key)
            if st.button("Submit feedback", key=f"submit_feedback_{msg_id}"):
                if not (st.session_state.get("is_authenticated") or st.session_state.get("authenticated")):
                    st.warning("Please login to send feedback")
                else:
                    feedback_text = (st.session_state.get(comment_key) or "").strip()
                    question = _latest_user_question(st.session_state.history, idx)
                    answer_text = content or ""
                    if not question or not answer_text:
                        st.warning("Nothing to send for feedback")
                    else:
                        rating = 1 if state.get("icon") == "like" else -1
                        if DEBUG_CHAT_UI:
                            logp(
                                "feedback_submit:start",
                                message_id=msg_id,
                                rating=rating,
                                has_note=bool(feedback_text),
                            )
                        mode_value = None
                        payload_value = None
                        if isinstance(meta, dict):
                            mode_value = meta.get("mode")
                            if DEBUG_CHAT_UI_STRICT:
                                payload_value = meta.get("raw_result")
                        rating = 1 if state.get("icon") == "like" else -1
                        if DEBUG_CHAT_UI:
                            logp(
                                "feedback_submit:start",
                                message_id=msg_id,
                                rating=rating,
                                has_note=bool(feedback_text),
                            )
                        mode_value = None
                        payload_value = None
                        if isinstance(meta, dict):
                            mode_value = meta.get("mode")
                            if DEBUG_CHAT_UI_STRICT:
                                payload_value = meta.get("raw_result")

                        user_id_raw = st.session_state.get("user_id", CONFIG.get("FEEDBACK_DEFAULT_USER_ID"))
                        user_id_value: Optional[int] = None
                        if user_id_raw not in (None, ""):
                            try:
                                user_id_value = int(user_id_raw)
                            except (TypeError, ValueError):
                                logp("feedback_submit:user_id_invalid", raw=user_id_raw)
                                user_id_value = None
                        session_identifier = st.session_state.get("feedback_session_id")
                        if not session_identifier:
                            session_identifier = str(uuid.uuid4())
                            st.session_state["feedback_session_id"] = session_identifier
                        category_value = "like" if rating > 0 else "dislike"
                        comment_text = feedback_text or answer_text or question or "Feedback"
                        metadata_payload: Dict[str, Any] = {
                            "question": question,
                            "answer_preview": (answer_text or "")[:200],
                            "mode": mode_value,
                            "message_id": msg_id,
                        }
                        if feedback_text:
                            metadata_payload["note"] = feedback_text
                        if payload_value and DEBUG_CHAT_UI_STRICT:
                            metadata_payload["raw_response"] = payload_value

                        try:
                            result = api_client.send_feedback(
                                user_id=user_id_value,
                                session_id=session_identifier,
                                rating=rating,
                                category=category_value,
                                comment=comment_text,
                                metadata=metadata_payload,
                                message_id=msg_id,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logp("feedback_submit:error", message_id=msg_id, error=str(exc))
                            st.error(f"Failed to save message feedback: {exc}")
                        else:
                            logp("feedback_submit:success", message_id=msg_id)
                            if isinstance(result, dict) and result.get("warning"):
                                st.warning(result["warning"])
                            st.success("Feedback saved.")
                            st.session_state[f"fb_done_{msg_id}"] = True
                            st.session_state.feedback_mode.pop(idx, None)
                            st.session_state.pop(comment_key, None)
                            st.session_state["last_feedback_ok"] = True
                            st.rerun()
    else:
        st.caption("How was this answer?")


def render(api_client, assistant_title: str, feedback_dir: str) -> None:
    st.header(assistant_title)

    st.session_state.setdefault("assistant_meta", [])

    user_prompt = st.chat_input("Ask a question...")
    if user_prompt:
        st.session_state.history.append(("user", user_prompt))
        with st.spinner("Thinking..."):
            result = api_client.chat(user_prompt)
        answer_text, answer_field = _select_answer_text(result)
        st.session_state.history.append(("assistant", (answer_text or "").strip()))
        st.session_state.assistant_meta.append(_build_answer_meta(result, answer_text, answer_field))
        st.rerun()

    assistant_counter = 0
    assistant_total = sum(1 for role, _ in st.session_state.history if role == "assistant")
    if len(st.session_state.assistant_meta) > assistant_total:
        st.session_state.assistant_meta = st.session_state.assistant_meta[-assistant_total:]

    for idx, (role, content) in enumerate(st.session_state.history):
        if role == "user":
            message(
                content,
                is_user=True,
                key=f"user_{idx}",
                avatar_style="bottts-neutral",
                seed="UserSeed",
            )
        elif role == "assistant":
            meta: Dict[str, Any] = (
                st.session_state.assistant_meta[assistant_counter]
                if assistant_counter < len(st.session_state.assistant_meta)
                else {}
            )
            assistant_counter += 1
            if not meta:
                logp("guard:missing_meta", idx=idx)

            payload_for_render: Optional[Dict[str, Any]] = meta.get("raw_result") if isinstance(meta, dict) else None
            if not payload_for_render:
                logp("guard:missing_raw_result", idx=idx)
                payload_for_render = {"answer": content}

            logp("answer_section:enter", idx=idx, meta_present=bool(meta))

            with st.container():
                answer_markdown, answer_source = render_primary_answer(payload_for_render)
                logp(
                    "answer_section:exit",
                    idx=idx,
                    source=answer_source,
                    len_answer=len(answer_markdown),
                )
                if DEBUG_CHAT_UI_STRICT:
                    with st.expander("Debug: Raw payload", expanded=False):
                        st.json(payload_for_render)
                    decision_debug = meta.get("decision_explain") or {}
                    debug_stats = {
                        "answer_source": answer_source,
                        "len_answer": len(answer_markdown),
                        "len_answer2": len(payload_for_render.get("answer2") or ""),
                        "len_answer3": len(payload_for_render.get("answer3") or ""),
                        "used_chunks_count": len(meta.get("used_chunks") or []),
                        "retrieved_chunks_count": len(payload_for_render.get("retrieved_chunks_metadata") or []),
                        "mode": meta.get("mode"),
                        "sim_max": decision_debug.get("max_similarity"),
                        "threshold_low": decision_debug.get("threshold_low"),
                        "threshold_high": decision_debug.get("threshold_high"),
                    }
                    with st.expander("Debug: Keys & lengths", expanded=False):
                        st.json(debug_stats)
                    logp("answer_debug_panels:shown", stats=debug_stats)
                _render_decision_details(meta or {})
                answer_for_feedback = answer_markdown or (payload_for_render.get("answer") or content or ANSWER_PLACEHOLDER)
                if not answer_markdown:
                    logp("guard:feedback_placeholder", idx=idx, source=answer_source)
                _render_feedback_controls(idx, answer_for_feedback, meta, api_client)
