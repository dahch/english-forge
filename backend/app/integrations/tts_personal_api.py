from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> bytes | None:
        ...


class TTSPersonalAPI(TTSProvider):
    """
    TTS via personal-api (Pocket TTS behind RQ/Redis).

    Flow:
        1. POST {PERSONAL_API_URL}/v1/speak {text, voice} → {job_id, status}
        2. Poll GET {PERSONAL_API_URL}/v1/jobs/{job_id} until finished/failed
        3. Decode audio_base64 → bytes

    NOTE: Before hardcoding the default voice, verify available voices against
    GET /v1/voices on Pocket TTS. The default "alba" is one of 8 built-in voices
    without auth, but the exact list should be confirmed.
    """

    def __init__(self):
        settings = get_settings()
        self._base_url = settings.PERSONAL_API_URL.rstrip("/")
        self._default_voice = settings.TTS_DEFAULT_VOICE
        self._poll_interval = settings.TTS_JOB_POLL_INTERVAL_MS / 1000.0
        self._timeout = settings.TTS_JOB_TIMEOUT_SECONDS

    async def synthesize(self, text: str, voice: str | None = None) -> bytes | None:
        if not text.strip():
            return None

        voice = voice or self._default_voice

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/v1/speak",
                json={"text": text, "voice": voice},
            )
            resp.raise_for_status()
            data = resp.json()

        job_id = data.get("job_id")
        if not job_id:
            logger.error("No job_id returned by /v1/speak")
            return None

        elapsed = 0.0
        async with httpx.AsyncClient(timeout=30.0) as client:
            while elapsed < self._timeout:
                await asyncio.sleep(self._poll_interval)
                elapsed += self._poll_interval

                poll_resp = await client.get(f"{self._base_url}/v1/jobs/{job_id}")
                poll_resp.raise_for_status()
                job_data = poll_resp.json()

                status = job_data.get("status", "")

                if status == "finished":
                    result = job_data.get("result", {})
                    if "error" in result:
                        logger.error(f"TTS job failed (Pocket returned error): {result['error']}")
                        return None
                    audio_b64 = result.get("audio_base64")
                    if not audio_b64:
                        logger.error("TTS job finished but no audio_base64 in result")
                        return None
                    import base64
                    return base64.b64decode(audio_b64)

                if status == "failed":
                    result = job_data.get("result", {})
                    error_msg = result.get("error", "Unknown error")
                    logger.error(f"TTS job failed: {error_msg}")
                    return None

        logger.error(f"TTS job {job_id} timed out after {self._timeout}s")
        return None
