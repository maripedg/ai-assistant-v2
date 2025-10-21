from datetime import datetime

import streamlit as st
from streamlit_chat import message

from services import storage


def _latest_user_question(history, current_index):
    for idx in range(current_index - 1, -1, -1):
        role, content = history[idx]
        if role == "user":
            return content
    return ""


def render(api_client, assistant_title: str, feedback_dir: str):
    st.header(assistant_title)

    user_prompt = st.chat_input("Ask a question...")
    if user_prompt:
        st.session_state.history.append(("user", user_prompt))
        with st.spinner("Thinking..."):
            result = api_client.chat(user_prompt)
        st.session_state.metadata = result.get("retrieved_chunks_metadata", [])
        st.session_state.history.append(("assistant", result.get("answer", "")))
        if result.get("answer2"):
            st.session_state.history.append(("assistant2", result["answer2"]))
        st.rerun()

    # Render historial
    for idx, (role, content) in enumerate(st.session_state.history):
        if role == "user":
            message(
                content,
                is_user=True,
                key=f"user_{idx}",
                avatar_style="bottts-neutral",
                seed="UserSeed",
            )
        elif role in {"assistant", "assistant2"}:
            message(
                content,
                key=f"assistant_{idx}",
                avatar_style="bottts",
                seed="AssistantSeed",
            )
            # Feedback controls
            msg_id = f"{idx}_{abs(hash(content)) % 1000000}"
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
            elif state.get("icon"):
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
                            username = st.session_state.get("username") or st.session_state.get("auth_user") or ""
                            question = _latest_user_question(st.session_state.history, idx)
                            answer_text = content or ""
                            if not question or not answer_text:
                                st.warning("Nothing to send for feedback")
                            else:
                                try:
                                    result = storage.feedback_thumb(
                                        username=username,
                                        question=question,
                                        answer=answer_text,
                                        is_like=(state["icon"] == "like"),
                                        comment=feedback_text,
                                        ts=None,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    st.error(f"Failed to save message feedback: {exc}")
                                else:
                                    if isinstance(result, dict) and result.get("warning"):
                                        st.warning(result["warning"])
                                    st.session_state[f"fb_done_{msg_id}"] = True
                                    st.session_state.feedback_mode.pop(idx, None)
                                    st.session_state.pop(comment_key, None)
                                    st.session_state["last_feedback_ok"] = True
                                    st.rerun()
            else:
                st.caption("How was this answer?")

    # Metadatos
    if st.session_state.metadata:
        st.markdown("---")
        with st.expander("Source Documents", expanded=False):
            st.json(st.session_state.metadata)
