import os
from dotenv import load_dotenv
from pathlib import Path

# Carga .env local (desarrollo)
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

DEFAULT_ASSISTANT_TITLE = "RODOD / DBE Assistant"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def get_config():
    # Defaults seguros
    backend = os.getenv("BACKEND_API_BASE", "http://localhost:5000")
    session_secret = os.getenv("SESSION_SECRET")
    rag_assets_dir = os.getenv("RAG_ASSETS_DIR", "data/rag-assets")
    chat_figures_debug = os.getenv("CHAT_FIGURES_DEBUG", "0")
    figure_max_width_px = _int_env("FIGURE_MAX_WIDTH_PX", 900)
    return {
        "BACKEND_API_BASE": backend.rstrip("/"),
        "FRONTEND_PORT": _int_env("FRONTEND_PORT", 8501),
        "AUTH_STORAGE_DIR": os.getenv("AUTH_STORAGE_DIR", "./data/credenciales"),
        "FEEDBACK_STORAGE_DIR": os.getenv("FEEDBACK_STORAGE_DIR", "./data/feedback"),
        "ASSISTANT_TITLE": os.getenv("ASSISTANT_TITLE", DEFAULT_ASSISTANT_TITLE),
        "SESSION_TTL_MIN": _int_env("SESSION_TTL_MIN", 120),
        "SESSION_COOKIE_NAME": os.getenv("SESSION_COOKIE_NAME", "assistant_session"),
        "SESSION_SECRET": session_secret,
        "REQUEST_TIMEOUT": _int_env("REQUEST_TIMEOUT", 60),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO").upper(),
        "RAG_ASSETS_DIR": rag_assets_dir,
        "CHAT_FIGURES_DEBUG": chat_figures_debug,
        "FIGURE_MAX_WIDTH_PX": figure_max_width_px,
        # Auth/Feedback toggles
        "AUTH_MODE": os.getenv("AUTH_MODE", "local"),              # local | db
        "FEEDBACK_MODE": os.getenv("FEEDBACK_MODE", "local"),      # local | db
        "DUAL_WRITE_FEEDBACK": _bool_env("DUAL_WRITE_FEEDBACK", False),
        "DEBUG_CHAT_UI": _bool_env("DEBUG_CHAT_UI", False),
        "DEBUG_CHAT_UI_STRICT": _bool_env("DEBUG_CHAT_UI_STRICT", False),
    }
