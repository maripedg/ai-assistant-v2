from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


class UsageRepoDB:
    @staticmethod
    def log_login(
        db: Session,
        *,
        user_id: Optional[int],
        email: Optional[str],
        client: str,
        ui_version: Optional[str],
        ip: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        stmt = text(
            """
            INSERT INTO AUTH_LOGINS (USER_ID, EMAIL, CLIENT, UI_VERSION, IP, USER_AGENT)
            VALUES (:user_id, :email, :client, :ui_version, :ip, :user_agent)
            """
        )
        params: Dict[str, Any] = {
            "user_id": user_id,
            "email": email,
            "client": client,
            "ui_version": ui_version,
            "ip": ip,
            "user_agent": user_agent,
        }
        db.execute(stmt, params)
        logger.debug(
            "usage.log_login inserted client=%s ui_version=%s user_id=%s",
            client,
            ui_version,
            user_id,
        )

    @staticmethod
    def upsert_session(
        db: Session,
        *,
        session_id: str,
        user_id: Optional[int],
        client: str,
        ui_version: Optional[str],
    ) -> None:
        if not session_id:
            return
        update_stmt = text(
            """
            UPDATE CHAT_SESSIONS
               SET LAST_SEEN_AT = SYSTIMESTAMP
             WHERE ID = :session_id
            """
        )
        result = db.execute(update_stmt, {"session_id": session_id})
        if result.rowcount and result.rowcount > 0:
            logger.debug(
                "usage.upsert_session updated session_id=%s client=%s",
                session_id,
                client,
            )
            return

        insert_stmt = text(
            """
            INSERT INTO CHAT_SESSIONS (ID, USER_ID, CLIENT, UI_VERSION)
            VALUES (:session_id, :user_id, :client, :ui_version)
            """
        )
        params = {
            "session_id": session_id,
            "user_id": user_id,
            "client": client,
            "ui_version": ui_version,
        }
        db.execute(insert_stmt, params)
        logger.debug(
            "usage.upsert_session inserted session_id=%s client=%s",
            session_id,
            client,
        )

    @staticmethod
    def log_interaction(
        db: Session,
        *,
        session_id: Optional[str],
        user_id: Optional[int],
        message_id: Optional[str],
        question_text: Optional[str],
        answer_preview: Optional[str],
        resp_mode: Optional[str],
        sources_count: Optional[int],
        max_similarity: Optional[float],
        latency_ms: Optional[int],
        tokens_prompt: Optional[int],
        tokens_completion: Optional[int],
        cost_usd: Optional[Decimal],
        feedback_id: Optional[int],
        client: Optional[str],
        ui_version: Optional[str],
    ) -> None:
        stmt = text(
            """
            INSERT INTO CHAT_INTERACTIONS (
                SESSION_ID,
                USER_ID,
                MESSAGE_ID,
                QUESTION_TEXT,
                ANSWER_PREVIEW,
                RESP_MODE,
                SOURCES_COUNT,
                MAX_SIMILARITY,
                LATENCY_MS,
                TOKENS_PROMPT,
                TOKENS_COMPLETION,
                COST_USD,
                FEEDBACK_ID,
                CLIENT,
                UI_VERSION
            )
            VALUES (
                :session_id,
                :user_id,
                :message_id,
                :question_text,
                :answer_preview,
                :resp_mode,
                :sources_count,
                :max_similarity,
                :latency_ms,
                :tokens_prompt,
                :tokens_completion,
                :cost_usd,
                :feedback_id,
                :client,
                :ui_version
            )
            """
        )
        params: Dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "message_id": message_id,
            "question_text": question_text,
            "answer_preview": answer_preview,
            "resp_mode": resp_mode,
            "sources_count": sources_count,
            "max_similarity": max_similarity,
            "latency_ms": latency_ms,
            "tokens_prompt": tokens_prompt,
            "tokens_completion": tokens_completion,
            "cost_usd": cost_usd,
            "feedback_id": feedback_id,
            "client": client,
            "ui_version": ui_version,
        }
        db.execute(stmt, params)
        logger.debug(
            "usage.log_interaction inserted session_id=%s resp_mode=%s sources=%s",
            session_id,
            resp_mode,
            sources_count,
        )
