from __future__ import annotations

import os
from fastapi import APIRouter
from backend.core.db.engine import get_engine, resolve_db_url, mask_url, whoami

router = APIRouter(prefix="/api/_debug", tags=["_debug"])


@router.get("/db")
def db_debug():
    url, source = resolve_db_url()
    with get_engine().connect() as conn:
        info = whoami(conn)
    return {
        "effective_url": mask_url(url),
        "source": source,
        **info,
    }

