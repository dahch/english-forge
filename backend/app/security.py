from __future__ import annotations

import hashlib
import base64
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
import bcrypt

from app.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def _get_fernet_key() -> bytes:
    settings = get_settings()
    key = settings.SETTINGS_ENCRYPTION_KEY
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET_KEY.encode()).digest())
    if isinstance(key, str):
        key = key.encode()
    return key


from cryptography.fernet import Fernet


def encrypt_value(plain: str) -> str:
    f = Fernet(_get_fernet_key())
    return f.encrypt(plain.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    f = Fernet(_get_fernet_key())
    return f.decrypt(encrypted.encode()).decode()
