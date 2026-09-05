from __future__ import annotations

import os

from cryptography.fernet import Fernet

from app.config import get_settings


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.SETTINGS_ENCRYPTION_KEY
    if not key:
        import hashlib
        import base64
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.JWT_SECRET_KEY.encode()).digest()
        ).decode()
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt_value(plain: str) -> str:
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    f = _get_fernet()
    return f.decrypt(encrypted.encode()).decode()
