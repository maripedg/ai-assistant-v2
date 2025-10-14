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
from backend.app.deps import settings, validate_startup

app = FastAPI(title="AI Assistant Backend")

validate_startup(True)

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

# Nota: /chat implementa retrieval híbrido (DB→LLM) sin colas.
