from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.security import MAX_PASSWORD_BYTES

# Deliberately simple: we only need to reject obvious non-emails, not implement
# RFC 5322. EmailStr would add the email-validator dependency for little gain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD_LENGTH = 10


def _validate_password(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password is too long: {len(encoded)} bytes (max {MAX_PASSWORD_BYTES}). "
            "Use a passphrase under the limit."
        )
    return value


class UserRegister(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES)
    display_name: str = Field(default="", max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise ValueError("Enter a valid email address")
        return normalized

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        # bcrypt truncates past 72 bytes; reject instead of silently accepting
        # a password whose tail is ignored.
        return _validate_password(value)  # type: ignore[return-value]


class UserLogin(BaseModel):
    # No format/min-length validation: existing accounts may predate the
    # stricter policy, and telling the caller which rule failed helps account
    # enumeration. Just normalize.
    email: str = Field(..., max_length=255)
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    current_level: str
    assessment_completed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)
    password: Optional[str] = Field(
        default=None, min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_BYTES
    )
    # Required whenever `password` is set: a stolen token must not be enough to
    # take over the account by changing its password.
    current_password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: Optional[str]) -> Optional[str]:
        return _validate_password(value)
