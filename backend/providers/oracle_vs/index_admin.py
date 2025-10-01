
"""Oracle Vector Store administration helpers."""
from __future__ import annotations

from typing import Any, Optional


def _lazy_import_oracledb():
    try:
        import oracledb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "The 'oracledb' package is required for Oracle index administration. "
            "Install it via `pip install oracledb`."
        ) from exc
    return oracledb


def ensure_index_table(conn: Any, index_name: str, distance_metric: str) -> None:
    """Create the vector index table if it is missing."""

    ddl = f"""
    CREATE TABLE {index_name} (
        chunk_id      VARCHAR2(128) PRIMARY KEY,
        doc_id        VARCHAR2(128),
        text_content  CLOB,
        embedding     BLOB,
        metadata_json CLOB,
        hash_norm     VARCHAR2(128),
        distance_metric VARCHAR2(32),
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """

    _lazy_import_oracledb()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM user_tables WHERE table_name = :1",
            (index_name.upper(),),
        )
        exists = cur.fetchone()[0] > 0
        if not exists:
            cur.execute(ddl)
            cur.execute(
                f"INSERT INTO {index_name} (chunk_id, distance_metric) VALUES (:chunk_id, :metric)",
                ("__INIT__", distance_metric),
            )
            cur.execute(f"DELETE FROM {index_name} WHERE chunk_id = :chunk_id", ("__INIT__",))
            conn.commit()


def ensure_alias(conn: Any, alias_name: str, index_name: str) -> None:
    """Create or replace a synonym pointing to the given index table."""

    statements = [
        f"CREATE OR REPLACE VIEW {alias_name} AS SELECT * FROM {index_name}",
    ]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
