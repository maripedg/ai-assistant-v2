from __future__ import annotations

import base64
import os
import hashlib
from typing import Literal

try:
    import bcrypt  # type: ignore
except Exception:  # pragma: no cover
    bcrypt = None  # type: ignore


Algo = Literal["bcrypt", "pbkdf2_sha256"]


def hash_password(plain: str, algo: Algo = "bcrypt") -> str:
    if algo == "bcrypt" and bcrypt is not None:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")
    # Fallback PBKDF2-HMAC-SHA256
    iterations = 390000
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(plain: str, stored_hash: str, algo: Algo = "bcrypt") -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        # Force PBKDF2 path regardless of requested algo
        try:
            _, it_s, salt_b64, hash_b64 = stored_hash.split("$", 3)
            iterations = int(it_s)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
            return hashlib.compare_digest(dk, expected)
        except Exception:
            return False
    if algo == "bcrypt" and bcrypt is not None:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    # PBKDF2 fallback if marker missing
    return verify_password(plain, stored_hash, algo="pbkdf2_sha256")

