from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import get_settings

ALGO = "HS256"


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + timedelta(minutes=s.token_ttl_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])


SECRET_KEYS = ("password", "passwd", "token", "secret", "authorization", "api_key", "apikey", "cookie", "dni", "cuil")


def redact(obj):
    """Depura secretos/PII conocidos antes de persistir evidencias o logs."""
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in SECRET_KEYS) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
