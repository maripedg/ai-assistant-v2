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

            state = st.session_state.feedback_mode.setdefault(idx, {"icon": None})

            cols = st.columns([1, 1, 6], gap="small")
            like_clicked = cols[0].button("\U0001F44D", key=f"like_{idx}")
            dislike_clicked = cols[1].button("\U0001F44E", key=f"dislike_{idx}")
            if like_clicked:
                state["icon"] = "like"
                state["needs_reset"] = True
                st.session_state.feedback_mode[idx] = state
            if dislike_clicked:
                state["icon"] = "dislike"
                state["needs_reset"] = True
                st.session_state.feedback_mode[idx] = state

            if state.get("icon"):
                comment_key = f"feedback_comment_{idx}"

                if state.pop("needs_reset", None):
                    st.session_state.pop(comment_key, None)
                    st.session_state.feedback_mode[idx] = state

                with cols[2]:
                    st.text_area("Comment (optional)", key=comment_key)
                    if st.button("Submit feedback", key=f"submit_feedback_{idx}"):
                        feedback_text = st.session_state.get(comment_key, "").strip()
                        record = {
                            "username": st.session_state.auth_user,
                            "question": _latest_user_question(st.session_state.history, idx),
                            "answer": content,
                            "icon": state["icon"],
                            "feedback": feedback_text,
                            "ts": datetime.utcnow().isoformat(timespec="seconds"),
                        }
                        try:
                            storage.append_icon_feedback(feedback_dir, record)
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Failed to save message feedback: {exc}")
                        else:
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