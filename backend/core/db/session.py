from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import sessionmaker

from .engine import get_engine


SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator:
    """FastAPI dependency that yields a DB session."""
    with session_scope() as s:
        yield s

