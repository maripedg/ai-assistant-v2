from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    ForeignKey,
    Index,
    Identity,
    TIMESTAMP,
    func,
)

from backend.core.db.base import Base


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    session_id = Column(String(100), nullable=True)
    rating = Column(Integer, nullable=True)
    category = Column(String(50), nullable=True)
    # Map attribute 'comment' to DB column COMMENT_TEXT (avoid reserved keyword)
    comment = Column("COMMENT_TEXT", Text, nullable=True)
    # Map attribute to DB column METADATA; keep attribute name safe (metadata_json)
    metadata_json = Column("METADATA", Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.systimestamp(), nullable=False)

    __table_args__ = (
        Index("ix_feedback_user_created", "user_id", "created_at"),
        Index("ix_feedback_created", "created_at"),
    )
