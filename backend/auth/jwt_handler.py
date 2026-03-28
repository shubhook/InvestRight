import os
from datetime import datetime, timezone, timedelta
from typing import Optional
import jwt

_SECRET = os.getenv("JWT_SECRET")
if not _SECRET:
    raise EnvironmentError(
        "JWT_SECRET environment variable is not set. "
        "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )

_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", 24))


def generate_token(payload: dict) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        **payload,
        "iat": now,
        "exp": now + timedelta(hours=_EXPIRY_HOURS),
    }
    return jwt.encode(claims, _SECRET, algorithm="HS256")


def verify_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _SECRET, algorithms=["HS256"])
    except Exception:
        return None
