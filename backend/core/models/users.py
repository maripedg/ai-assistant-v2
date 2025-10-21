from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Text,
    UniqueConstraint,
    Identity,
)
from sqlalchemy.sql import func

from backend.core.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    email = Column(String(320), nullable=False)
    name = Column(String(255), nullable=True)
    role = Column(String(50), nullable=False, default="user")
    status = Column(String(32), nullable=False, default="invited")
    auth_provider = Column(String(20), nullable=False, default="local")
    password_hash = Column(String(255), nullable=True)
    password_algo = Column(String(20), nullable=False, default="bcrypt")
    password_updated_at = Column(DateTime(timezone=True), nullable=True)
    auth_sub = Column(String(255), nullable=True)
    auth_issuer = Column(String(255), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
    )
