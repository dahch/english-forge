"""Audio file storage.

TTS output used to be stored as a base64 data URI in a TEXT column, then
serialized into every message list — megabytes per session and a schema lie
(`Message.audio_url` was VARCHAR(500)). New audio is written to `MEDIA_DIR`
and referenced as `media:<key>`; legacy data URIs remain readable so existing
databases keep working without a migration.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from app.config import get_settings

_MEDIA_PREFIX = "media:"
_KEY_RE = re.compile(r"^[0-9a-f]{32}\.(?:mp3|wav|webm|ogg|m4a)$")


def media_dir() -> Path:
    path = Path(get_settings().MEDIA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_audio_path(key: str) -> Path | None:
    """Only accept keys this module generated (no path traversal)."""
    if not _KEY_RE.fullmatch(key):
        return None
    candidate = media_dir() / key
    return candidate if candidate.is_file() else None


def store_audio(data: bytes, suffix: str = ".mp3") -> str:
    """Persist audio bytes and return the `media:<key>` reference."""
    import uuid

    key = f"{uuid.uuid4().hex}{suffix}"
    (media_dir() / key).write_bytes(data)
    return f"{_MEDIA_PREFIX}{key}"


def read_audio(reference: str | None) -> bytes | None:
    """Resolve a stored reference (media key or legacy data URI) to bytes."""
    if not reference:
        return None
    if reference.startswith("data:"):
        _, _, payload = reference.partition(",")
        try:
            return base64.b64decode(payload)
        except Exception:
            return None
    if reference.startswith(_MEDIA_PREFIX):
        path = safe_audio_path(reference[len(_MEDIA_PREFIX):])
        if path is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None
    return None


def has_audio(reference: str | None) -> bool:
    if not reference:
        return False
    if reference.startswith("data:"):
        return True
    if reference.startswith(_MEDIA_PREFIX):
        return safe_audio_path(reference[len(_MEDIA_PREFIX):]) is not None
    return False
