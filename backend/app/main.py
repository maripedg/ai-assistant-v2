import logging

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s:%(lineno)d | %(message)s"
)
logging.getLogger("backend.core.services.retrieval_service").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
# --- End logging setup ---

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.routers import health, chat
from backend.app.routers import auth as auth_router
from backend.app.routers import users as users_router
from backend.app.routers import feedback as feedback_router
from backend.app.routers import debug as debug_router
from backend.app.routers import ingest as ingest_router
from backend.app.routers import sharepoint as sharepoint_router
from backend.app.deps import settings, validate_startup
from backend.app.services.scheduler import start_scheduler, shutdown_scheduler
import os

app = FastAPI(title="AI Assistant Backend")

validate_startup(True)

# Ensure DB tables exist (auto-create if migrations not applied)
try:
    from backend.core.db.engine import get_engine
    from backend.core.db.base import Base
    import backend.core.models  # noqa: F401 - register models
    engine = get_engine()
    # Best-effort: create missing tables (ignore if exist)
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as _e:
        logging.getLogger(__name__).warning("DB init partial (create_all): %s", _e)
    # whoami log
    try:
        from backend.core.db.engine import whoami as _db_whoami
        with engine.connect() as _c:
            info = _db_whoami(_c)
            logging.getLogger(__name__).info(
                "DB whoami: service=%s schema=%s", info.get("service_name"), info.get("current_schema")
            )
    except Exception as _e:
        logging.getLogger(__name__).warning("DB whoami failed: %s", _e)
except Exception as _exc:  # noqa: BLE001 - non-fatal
    logging.getLogger(__name__).warning("DB init skipped: %s", _exc)

# CORS
cfg = settings.app.get("server", {}).get("cors", {})
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.get("allow_origins", ["*"]),
    allow_methods=cfg.get("allow_methods", ["*"]),
    allow_headers=cfg.get("allow_headers", ["*"]),
)

# Routers
app.include_router(health.router)
app.include_router(chat.router, prefix="")
app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
features = settings.app.get("features", {}) if isinstance(settings.app, dict) else {}
if features.get("users_api", True):
    app.include_router(users_router.router)
if features.get("feedback_api", True):
    app.include_router(feedback_router.router)
app.include_router(ingest_router.router, prefix="/api/v1")
app.include_router(sharepoint_router.router, prefix="/api/v1")

# Dev-only debug endpoints
debug_flag = False
try:
    debug_flag = bool((settings.app or {}).get("debug", False))  # type: ignore[union-attr]
except Exception:
    debug_flag = False
if not debug_flag and os.getenv("ENV", "").lower() in {"dev", "development", "local"}:
    debug_flag = True
if debug_flag:
    app.include_router(debug_router.router)

# Nota: /chat implementa retrieval híbrido (DB→LLM) sin colas.

@app.on_event("startup")
def _startup_scheduler() -> None:
    start_scheduler()


@app.on_event("shutdown")
def _shutdown_scheduler() -> None:
    shutdown_scheduler()
