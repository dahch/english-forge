from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.config import get_settings

logger = logging.getLogger(__name__)


class STTWhisperServer:
    """
    Server-side Whisper STT using faster-whisper, running directly in the
    EnglishForge backend process. No dependency on personal-api or RQ.

    This is used as:
    - Fallback when Web Speech API is not available in the browser
    - Alternative for users who want server-side processing without Moonshine

    Default model: "small" (configurable via WHISPER_MODEL env var).
    Models are downloaded on first use and cached locally.
    """

    def __init__(self):
        settings = get_settings()
        self._model_size = settings.WHISPER_MODEL
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Add 'faster-whisper' to requirements.txt or use a different STT mode."
            )

    async def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str | None:
        import asyncio
        import tempfile
        import os

        self._load_model()

        suffix = ".wav" if "wav" in content_type else ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name

        try:
            segments, info = await asyncio.to_thread(
                self._model.transcribe, temp_path, beam_size=5
            )
            text = " ".join(segment.text for segment in segments).strip()
            return text if text else None
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None
        finally:
            os.unlink(temp_path)
