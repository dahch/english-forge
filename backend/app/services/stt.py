"""Shared STT entry point with CPU-concurrency control.

Moonshine (behind personal-api) saturates ~6 CPU cores with a handful of
concurrent jobs, and this box also runs the app, TTS and everything else — so
all assessment transcriptions go through a global semaphore (1 job at a time).
If personal-api is down or fails, fall back to the in-process faster-whisper
server (already available in the backend, see STTWhisperServer).
"""

from __future__ import annotations

import asyncio
import logging

from app.integrations.stt_personal_api import STTPersonalAPI
from app.integrations.stt_whisper_server import STTWhisperServer

logger = logging.getLogger(__name__)

# One transcription at a time across the whole process. Clips are short
# (≤30s), so worst-case queue wait stays a few seconds. NOTE: this is a
# per-process guard — it assumes the documented single uvicorn worker; a
# multi-worker deployment would need a cross-process lock to keep the
# CPU-saturation guarantee.
_STT_SEMAPHORE = asyncio.Semaphore(1)


async def transcribe_audio(
    audio_bytes: bytes,
    content_type: str = "audio/webm",
    provider: str | None = None,
) -> dict:
    """Transcribe a clip, returning {"text": str, "words": list[dict]}.

    `words` carries word timestamps ([{word, start, end}]) when the provider
    returns them; empty when not available. Never raises — an empty result
    means every provider failed.

    `provider` selects the engine in personal-api ('moonshine' | 'cartesia').
    `None` omits the field so the server keeps its default (moonshine),
    identical to the pre-provider behaviour. The in-process faster-whisper
    fallback is provider-agnostic: it only runs when personal-api itself
    fails, so a Cartesia outage degrades exactly like a Moonshine one.
    """
    if not audio_bytes:
        return {"text": "", "words": []}

    async with _STT_SEMAPHORE:
        # Primary: personal-api (Moonshine or Cartesia, behind RQ).
        try:
            result = await STTPersonalAPI().transcribe_detailed(
                audio_bytes, content_type, provider=provider
            )
            if result.get("text", "").strip():
                return result
            logger.warning("personal-api STT returned empty text, trying fallback")
        except Exception as e:
            logger.warning(f"personal-api STT failed, falling back to in-process whisper: {e}")

        # Fallback: faster-whisper in-process (no word timestamps).
        try:
            text = await STTWhisperServer().transcribe(audio_bytes, content_type)
            if text and text.strip():
                return {"text": text.strip(), "words": []}
        except Exception as e:
            logger.error(f"In-process whisper STT also failed: {e}")

    return {"text": "", "words": []}
