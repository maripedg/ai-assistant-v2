
"""Oracle Vector Store administration helpers."""
from __future__ import annotations

import logging
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


def ensure_index_table(conn: Any, index_name: str, distance_metric: str, dim: Optional[int] = None) -> None:
    """Ensure a legacy‑compatible physical table exists with VECTOR embeddings.

    The table is created with the following shape:

        ID RAW(16) PRIMARY KEY,
        TEXT CLOB,
        METADATA JSON,
        EMBEDDING VECTOR(dim),
        HASH_NORM VARCHAR2(128),
        DISTANCE_METRIC VARCHAR2(32),
        CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP

    If the table already exists, validates that EMBEDDING is VECTOR(dim).
    """

    if dim is None or int(dim) <= 0:
        raise ValueError("ensure_index_table requires a positive 'dim' (embedding dimension)")

    _lazy_import_oracledb()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM user_tables WHERE table_name = :1",
            (index_name.upper(),),
        )
        exists = cur.fetchone()[0] > 0

        if not exists:
            ddl = (
                f"CREATE TABLE {index_name} (\n"
                f"  ID RAW(16) PRIMARY KEY,\n"
                f"  TEXT CLOB,\n"
                f"  METADATA JSON,\n"
                f"  EMBEDDING VECTOR({int(dim)}),\n"
                f"  HASH_NORM VARCHAR2(128),\n"
                f"  DISTANCE_METRIC VARCHAR2(32),\n"
                f"  CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
                f"  UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
                f")"
            )
            cur.execute(ddl)
            conn.commit()
            logger.info("Created index table %s with VECTOR(%d)", index_name, int(dim))
        else:
            # Validate existing table has EMBEDDING as VECTOR using USER_TAB_COLUMNS
            cur.execute(
                """
                SELECT data_type
                  FROM user_tab_columns
                 WHERE table_name = :tbl
                   AND column_name = 'EMBEDDING'
                """,
                {"tbl": index_name.upper()},
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"Existing table {index_name} does not have column 'EMBEDDING'"
                )
            data_type = (row[0] or "").upper()
            if data_type != "VECTOR":
                raise ValueError(
                    f"Existing table {index_name} has EMBEDDING of type {data_type}; expected VECTOR"
                )

            # Try to detect dimension from data
            found_dim: Optional[int] = None
            try:
                cur.execute(f"SELECT VECTOR_DIMENSION(EMBEDDING) FROM {index_name} WHERE ROWNUM = 1")
                r = cur.fetchone()
                if r and r[0] is not None:
                    found_dim = int(r[0])
            except Exception:  # noqa: BLE001 - function may not be available or table empty
                found_dim = None

            if found_dim is not None and dim is not None and int(dim) > 0 and found_dim != int(dim):
                raise ValueError(
                    f"Existing table {index_name} has EMBEDDING VECTOR({found_dim}); expected VECTOR({dim}). "
                    f"Drop and recreate with the correct dimension."
                )

            if found_dim is None:
                logger.warning(
                    "Validated %s: EMBEDDING is VECTOR; could not verify dimension (no rows or unsupported function). Expected dim=%s; continuing.",
                    index_name,
                    str(dim) if dim is not None else "unknown",
                )
            else:
                logger.info(
                    "Validated %s: EMBEDDING is VECTOR; dim=%d, metric=%s",
                    index_name,
                    found_dim,
                    distance_metric,
                )


def ensure_alias(conn: Any, alias_name: str, index_name: str) -> None:
    """Create or replace a projection view exposing the legacy 4-column shape.

    The alias view always projects the stable interface:
      (ID, TEXT, METADATA, EMBEDDING)
    """
    with conn.cursor() as cur:
        # Preflight: if the name exists as a non-VIEW object, instruct to drop/rename
        cur.execute(
            "SELECT object_type FROM user_objects WHERE object_name = :1",
            (alias_name.upper(),),
        )
        row = cur.fetchone()
        if row:
            obj_type = (row[0] or "").upper()
            if obj_type != "VIEW":
                raise ValueError(
                    f"Cannot create alias view '{alias_name}': a {obj_type} with the same name already exists. "
                    f"Drop it or choose another alias."
                )

    # Normalize METADATA to CLOB for compatibility with LangChain OracleVS, which expects CLOB or LOB
    # rather than Python dicts when reading the metadata column.
    stmt = (
        f"CREATE OR REPLACE VIEW {alias_name} (ID, TEXT, METADATA, EMBEDDING) AS "
        f"SELECT ID, TEXT, JSON_SERIALIZE(METADATA RETURNING CLOB) AS METADATA, EMBEDDING FROM {index_name}"
    )
    with conn.cursor() as cur:
        cur.execute(stmt)
    conn.commit()
    logger.info("alias %s -> %s", alias_name, index_name)
logger = logging.getLogger(__name__)
