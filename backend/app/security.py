from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
import bcrypt

from app.config import Settings, get_settings
from app.integrations.crypto import (
    decrypt_value,
    encrypt_value,
    validate_encryption_settings,
)

logger = logging.getLogger(__name__)

# bcrypt silently truncates anything past 72 bytes: two different passwords
# sharing the first 72 bytes would authenticate interchangeably. Reject instead
# of truncating; the API schema caps the field as well.
MAX_PASSWORD_BYTES = 72

# Secrets shipped in the repo/docs. Running with any of them means anyone can
# forge tokens for any user, so they are treated as missing.
_WEAK_SECRETS = {
    "",
    "change-me-in-production-use-openssl-rand-hex-32",
    "change-me-to-a-random-64-char-hex-string",
    "changeme",
    "secret",
}
_MIN_SECRET_BYTES = 32


def validate_security_settings(settings: Settings) -> None:
    """Refuse to start with a weak/missing signing secret or a bad Fernet key.

    Called from the FastAPI lifespan before the database is touched. Tests and
    local experiments can opt out with ALLOW_INSECURE_DEFAULTS=true, which
    downgrades the JWT check to a loud warning.
    """
    validate_encryption_settings(settings)

    secret = (settings.JWT_SECRET_KEY or "").strip()
    weak = secret.lower() in _WEAK_SECRETS or len(secret.encode()) < _MIN_SECRET_BYTES
    if not weak:
        return

    message = (
        "JWT_SECRET_KEY is missing or too weak (need a random secret of at least "
        f"{_MIN_SECRET_BYTES} bytes). Generate one with: openssl rand -hex 32"
    )
    if settings.ALLOW_INSECURE_DEFAULTS:
        logger.warning("%s — continuing because ALLOW_INSECURE_DEFAULTS=true", message)
        return
    raise RuntimeError(message)


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password is too long: {len(raw)} bytes (max {MAX_PASSWORD_BYTES}). "
            "Shorten it or use a passphrase under the limit."
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    raw = plain.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raw = raw[:MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except ValueError:
        # Stored hash is not a valid bcrypt string (corrupt row) — treat as a
        # failed login instead of a 500 that leaks the difference.
        logger.error("Stored password hash is invalid; rejecting login")
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    to_encode = data.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + (
                expires_delta
                or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            ),
            "jti": str(uuid.uuid4()),
            "type": "access",
        }
    )
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        return None
    return payload


__all__ = [
    "MAX_PASSWORD_BYTES",
    "create_access_token",
    "decode_access_token",
    "decrypt_value",
    "encrypt_value",
    "hash_password",
    "validate_security_settings",
    "verify_password",
]
