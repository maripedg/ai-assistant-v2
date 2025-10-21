from __future__ import annotations

import os


def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # Fallback to SESSION_SECRET if provided
        secret = os.getenv("SESSION_SECRET", "changeme-secret")
    return secret


def jwt_ttl_min() -> int:
    try:
        return int(os.getenv("JWT_TTL_MIN", "1440"))
    except Exception:
        return 1440


def jwt_alg() -> str:
    return os.getenv("JWT_ALG", "HS256")

