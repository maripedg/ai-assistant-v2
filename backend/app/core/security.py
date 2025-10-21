from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict

import bcrypt


# -------- Password hashing --------
def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# -------- Minimal JWT (HS256) --------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign_hs256(secret: str, header_payload: bytes) -> str:
    sig = hmac.new(secret.encode("utf-8"), header_payload, hashlib.sha256).digest()
    return _b64url(sig)


def issue_jwt(user_id: int, email: str, role: str, ttl_min: int, *, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + max(1, int(ttl_min)) * 60,
    }
    header_b = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b}.{payload_b}".encode("utf-8")
    sig = _sign_hs256(secret, signing_input)
    return f"{header_b}.{payload_b}.{sig}"


def decode_jwt(token: str, *, secret: str) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".", 3)
    except ValueError as exc:  # noqa: PERF203
        raise ValueError("invalid_token") from exc
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = _sign_hs256(secret, signing_input)
    if not hmac.compare_digest(expected_sig, sig_b64):
        raise ValueError("invalid_signature")
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid_payload") from exc
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise ValueError("token_expired")
    return payload

