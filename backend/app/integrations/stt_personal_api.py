from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str | None:
        ...


class STTPersonalAPI(STTProvider):
    """
    STT via personal-api (Moonshine behind RQ/Redis).

    Flow:
        1. POST {PERSONAL_API_URL}/v1/transcribe (multipart, field "audio") → {job_id, status}
        2. Poll GET {PERSONAL_API_URL}/v1/jobs/{job_id} until finished/failed
        3. Read result.text

    WARNING: Moonshine saturates ~6 cores with only 5 concurrent requests and runs
    as a single replica (worker-stt). This mode is NOT recommended for live
    conversational STT — use it for post-session review where latency is acceptable.
    """

    def __init__(self):
        settings = get_settings()
        self._base_url = settings.PERSONAL_API_URL.rstrip("/")
        # Language hint for Moonshine: "en" produces real word timestamps
        # (the basis of the pronunciation fluency metrics). personal-api
        # <= old servers ignore the extra form field — backward safe.
        self._language = settings.STT_LANGUAGE
        self._poll_interval = settings.TTS_JOB_POLL_INTERVAL_MS / 1000.0
        self._timeout = settings.TTS_JOB_TIMEOUT_SECONDS

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str | None:
        result = await self.transcribe_detailed(audio_bytes, content_type)
        return result.get("text") or None

    async def transcribe_detailed(self, audio_bytes: bytes, content_type: str = "audio/wav") -> dict:
        """Transcribe returning {"text", "words"}.

        `words` is the Moonshine word-timestamp list ([{word, start, end}], in
        seconds) exposed by personal-api after the pronunciation-scoring
        upgrade. Empty list when the server predates that change — callers
        must treat it as optional.
        """
        if not audio_bytes:
            return {"text": "", "words": []}

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/v1/transcribe",
                files={"audio": ("audio.wav", audio_bytes, content_type)},
                data={"language": self._language},
            )
            resp.raise_for_status()
            data = resp.json()

        job_id = data.get("job_id")
        if not job_id:
            logger.error("No job_id returned by /v1/transcribe")
            return {"text": "", "words": []}

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
                        logger.error(f"STT job failed: {result['error']}")
                        return {"text": "", "words": []}
                    words = result.get("words") or []
                    if not isinstance(words, list):
                        words = []
                    return {"text": result.get("text", ""), "words": words}

                if status == "failed":
                    result = job_data.get("result", {})
                    error_msg = result.get("error", "Unknown error")
                    logger.error(f"STT job failed: {error_msg}")
                    return {"text": "", "words": []}

        logger.error(f"STT job {job_id} timed out after {self._timeout}s")
        return {"text": "", "words": []}
