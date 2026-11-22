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
        assert kwargs["json"]["model"] == "english_2026-04"
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


# --- provider selection ------------------------------------------------------
#
# `provider=None` must produce the exact pre-provider request (field omitted),
# so existing behaviour is preserved. When set, the field goes over the wire
# and provider-specific fields are not mixed (a Pocket model id or the "alba"
# voice would be rejected by Cartesia).


def _fake_client(calls, result_payload=None, audio_bytes=None):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return FakeResponse({"job_id": "job-x"})

        async def get(self, url):
            calls.append(("GET", url, {}))
            payload = result_payload or {"text": "", "words": []}
            if audio_bytes is not None:
                payload = {
                    "status": "finished",
                    "result": {"audio_base64": base64.b64encode(audio_bytes).decode()},
                }
            else:
                payload = {"status": "finished", "result": payload}
            return FakeResponse(payload)

    return FakeClient


class TestTTSProviderPayload:
    @pytest.mark.asyncio
    async def test_no_provider_omits_field_and_keeps_defaults(self, monkeypatch):
        """Sin provider, el payload es el de siempre (model + voice por defecto)."""
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert "provider" not in kwargs["json"]
        assert kwargs["json"]["model"] == get_settings().TTS_MODEL
        assert kwargs["json"]["voice"] == get_settings().TTS_DEFAULT_VOICE

    @pytest.mark.asyncio
    async def test_cartesia_sends_provider_without_pocket_model(self, monkeypatch):
        """Un model id de Pocket TTS enviado a Cartesia sería 400: no se manda."""
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello", provider="cartesia")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert kwargs["json"]["provider"] == "cartesia"
        assert "model" not in kwargs["json"]

    @pytest.mark.asyncio
    async def test_cartesia_does_not_forward_pocket_default_voice(self, monkeypatch):
        """'alba' solo existe en Pocket TTS: no debe viajar a Cartesia."""
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello", provider="cartesia")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert "voice" not in kwargs["json"]

    @pytest.mark.asyncio
    async def test_cartesia_ignores_pocket_builtin_voice(self, monkeypatch):
        """Regresión: `tts_voice` es un campo único compartido por ambos motores.
        Si el usuario tenía "alba" (Pocket) y cambia a Cartesia, ese nombre no
        debe viajar como voice id de Cartesia (daría 400)."""
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello", voice="alba", provider="cartesia")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert "voice" not in kwargs["json"]

    @pytest.mark.asyncio
    async def test_cartesia_forwards_explicit_voice(self, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello", voice="cartesia-voice-id", provider="cartesia")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert kwargs["json"]["voice"] == "cartesia-voice-id"

    @pytest.mark.asyncio
    async def test_explicit_pockettts_behaves_like_default(self, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, audio_bytes=b"mp3"))

        await TTSPersonalAPI().synthesize("hello", provider="pockettts")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert kwargs["json"]["provider"] == "pockettts"
        assert kwargs["json"]["model"] == get_settings().TTS_MODEL


class TestSTTProviderPayload:
    @pytest.mark.asyncio
    async def test_no_provider_omits_field(self, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, {"text": "hi", "words": []}))

        await STTPersonalAPI().transcribe_detailed(b"audio")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert "provider" not in kwargs["data"]
        assert kwargs["data"]["language"] == get_settings().STT_LANGUAGE

    @pytest.mark.asyncio
    async def test_cartesia_sends_provider_field(self, monkeypatch):
        calls = []
        monkeypatch.setattr(httpx, "AsyncClient", _fake_client(calls, {"text": "hi", "words": []}))

        await STTPersonalAPI().transcribe_detailed(b"audio", provider="cartesia")

        _, _, kwargs = [c for c in calls if c[0] == "POST"][0]
        assert kwargs["data"]["provider"] == "cartesia"
        assert kwargs["data"]["language"] == get_settings().STT_LANGUAGE
        # El audio sigue viajando como multipart.
        assert "files" in kwargs
