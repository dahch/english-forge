"""Wire-contract tests for the personal-api integrations.

These pin exactly what english-forge sends over the wire:
- POST /v1/speak always carries the pinned Pocket TTS model (TTS_MODEL).
- POST /v1/transcribe always carries the language hint (STT_LANGUAGE).
- The job result parsing tolerates old servers that return no `words`.
"""

import base64
import json

import httpx
import pytest

from app.config import get_settings
from app.integrations.stt_personal_api import STTPersonalAPI
from app.integrations.tts_personal_api import TTSPersonalAPI


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestTTSPayload:
    @pytest.mark.asyncio
    async def test_speak_always_sends_pinned_model(self, monkeypatch):
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def post(self, url, **kwargs):
                calls.append(("POST", url, kwargs))
                return FakeResponse({"job_id": "job-1"})

            async def get(self, url):
                calls.append(("GET", url, {}))
                audio_b64 = base64.b64encode(b"fake-mp3").decode()
                return FakeResponse({
                    "status": "finished",
                    "result": {"audio_base64": audio_b64},
                })

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        audio = await TTSPersonalAPI().synthesize("hello there", voice="alba")

        assert audio == b"fake-mp3"
        posts = [c for c in calls if c[0] == "POST"]
        assert len(posts) == 1
        _, url, kwargs = posts[0]
        assert url.endswith("/v1/speak")
        assert kwargs["json"]["model"] == get_settings().TTS_MODEL
        assert kwargs["json"]["model"] == "english_2026-04_24l"
        assert kwargs["json"]["voice"] == "alba"
        assert kwargs["json"]["text"] == "hello there"

    @pytest.mark.asyncio
    async def test_empty_text_skips_http(self, monkeypatch):
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def post(self, url, **kwargs):
                calls.append(("POST", url, kwargs))
                return FakeResponse({"job_id": "job-1"})

            async def get(self, url):
                raise AssertionError("should not poll")

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        assert await TTSPersonalAPI().synthesize("   ") is None
        assert calls == []


class TestSTTPayload:
    @pytest.mark.asyncio
    async def test_transcribe_sends_language_and_parses_words(self, monkeypatch):
        calls = []
        words = [{"word": "hello", "start": 0.0, "end": 0.4}, {"word": "there", "start": 0.5, "end": 0.9}]

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def post(self, url, **kwargs):
                calls.append(("POST", url, kwargs))
                return FakeResponse({"job_id": "job-2"})

            async def get(self, url):
                calls.append(("GET", url, {}))
                return FakeResponse({
                    "status": "finished",
                    "result": {"text": "hello there", "words": words},
                })

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        result = await STTPersonalAPI().transcribe_detailed(b"audio-bytes", "audio/webm")

        assert result == {"text": "hello there", "words": words}
        posts = [c for c in calls if c[0] == "POST"]
        assert len(posts) == 1
        _, url, kwargs = posts[0]
        assert url.endswith("/v1/transcribe")
        # Multipart audio + form field language.
        assert kwargs["data"]["language"] == get_settings().STT_LANGUAGE
        assert kwargs["data"]["language"] == "en"
        assert "files" in kwargs

    @pytest.mark.asyncio
    async def test_legacy_server_without_words_degrades(self, monkeypatch):
        """A personal-api predating the words upgrade must not break — the
        field arrives empty and pronunciation scoring loses only fluency."""

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def post(self, url, **kwargs):
                return FakeResponse({"job_id": "job-3"})

            async def get(self, url):
                # Old server: no words field at all, garbage types tolerated.
                return FakeResponse({"status": "finished", "result": {"text": "legacy"}})

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        result = await STTPersonalAPI().transcribe_detailed(b"audio-bytes")
        assert result == {"text": "legacy", "words": []}

    @pytest.mark.asyncio
    async def test_failed_job_returns_empty(self, monkeypatch):
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def post(self, url, **kwargs):
                return FakeResponse({"job_id": "job-4"})

            async def get(self, url):
                return FakeResponse({"status": "failed", "result": {"error": "boom"}})

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        result = await STTPersonalAPI().transcribe_detailed(b"audio-bytes")
        assert result == {"text": "", "words": []}
