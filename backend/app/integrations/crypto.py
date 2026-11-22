from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Fernet requires exactly 32 url-safe-base64 bytes (44 chars).
_FERNET_KEY_BYTES = 44


def _derive_key(secret: str) -> bytes:
    """Legacy/compat key: sha256(JWT secret), encoded as a Fernet key.

    Kept so databases created before SETTINGS_ENCRYPTION_KEY existed keep
    working. New deployments should set a dedicated key: rotating the JWT
    secret must never destroy stored provider credentials.
    """
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def encryption_mode(settings: Settings | None = None) -> str:
    """'dedicated' when SETTINGS_ENCRYPTION_KEY is set, 'derived' otherwise."""
    settings = settings or get_settings()
    return "dedicated" if (settings.SETTINGS_ENCRYPTION_KEY or "").strip() else "derived"


def validate_encryption_settings(settings: Settings) -> None:
    """Fail fast at startup on a malformed Fernet key.

    Without this, a typo in SETTINGS_ENCRYPTION_KEY only surfaces on the first
    provider save, as a 500 the user cannot diagnose.
    """
    key = (settings.SETTINGS_ENCRYPTION_KEY or "").strip()
    if not key:
        return
    try:
        Fernet(key.encode())
    except Exception as exc:  # noqa: BLE001 - any parse error is fatal config
        raise RuntimeError(
            "SETTINGS_ENCRYPTION_KEY is not a valid Fernet key "
            f"(expected {_FERNET_KEY_BYTES} url-safe base64 chars). Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    key = (settings.SETTINGS_ENCRYPTION_KEY or "").strip()
    if key:
        return Fernet(key.encode())
    logger.warning(
        "SETTINGS_ENCRYPTION_KEY is empty: provider API keys are encrypted with a key "
        "derived from JWT_SECRET_KEY. Rotating the JWT secret will make them unreadable. "
        "Set a dedicated SETTINGS_ENCRYPTION_KEY before rotating."
    )
    return Fernet(_derive_key(settings.JWT_SECRET_KEY))


def reset_fernet_cache() -> None:
    """Testing hook: drop the cached Fernet instance after config changes."""
    _fernet.cache_clear()


def encrypt_value(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()
