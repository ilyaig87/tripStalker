"""Authentication helpers: bcrypt password hashing and JWT access tokens.

Kept dependency-light on purpose — `bcrypt` and `PyJWT` directly, no passlib
(which warns loudly against modern bcrypt builds).
"""
from __future__ import annotations

import datetime as dt

import bcrypt
import jwt

from app.config import settings

# bcrypt only consumes the first 72 bytes of a password; longer inputs raise in
# recent builds, so we truncate consistently on hash *and* verify.
_MAX_PW_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_MAX_PW_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX_PW_BYTES], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, email: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """Return the token payload, or None if it is invalid/expired."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
