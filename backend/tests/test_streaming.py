"""Incremental conversation turns (SSE service + transport)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.prompts import (
    build_correction_analysis_prompt,
    build_streaming_system_prompt,
)
from app.llm.router import LLMRouter
from app.models.models import Assessment, Base, Correction, Message, Session, User
from app.routers.messages import stream_message
from app.schemas.session import MessageCreate
from app.services import media_store
from app.services.conversation_stream import stream_turn


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(email="stream@example.com", hashed_password="x", display_name="Stream")
    db.add(u)
    await db.flush()
    return u


@pytest_asyncio.fixture
async def session(db: AsyncSession, user: User) -> Session:
    s = Session(user_id=user.id, cefr_level="B1")
    db.add(s)
    await db.flush()
    return s


def _install_fakes(monkeypatch, *, chunks=("Hel", "lo"), tts=b"audio"):
    async def fake_stream(self, messages, system_prompt, **kwargs):
        for chunk in chunks:
            yield chunk

    async def fake_complete(self, messages, system_prompt, task="correction", **kwargs):
        return {
            "content": json.dumps(
                {
                    "corrections": [
                        {
                            "error_type": "grammar",
                            "original": "I go",
                            "correction": "I went",
                            "explanation": "Past tense",
                        }
                    ],
                    "new_vocab": [
                        {"word": "serendipity", "definition": "luck", "example": "pure serendipity"}
                    ],
                }
            ),
            "provider": "mock",
            "model": "mock",
        }

    async def fake_tts(self, text, voice=None):
        return tts

    monkeypatch.setattr(LLMRouter, "stream_with_fallback", fake_stream)
    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)
    monkeypatch.setattr(TTSPersonalAPI, "synthesize", fake_tts)


def test_streaming_prompt_is_plain_text_and_review_prompt_is_json():
    streaming = build_streaming_system_prompt(cefr_level="B1")
    assert "plain text" in streaming
    assert "valid JSON" not in streaming

    review = build_correction_analysis_prompt("I go to school", "B1")
    assert "corrections" in review and "new_vocab" in review


@pytest.mark.asyncio
async def test_stream_turn_emits_deltas_then_persists(
    db: AsyncSession, user: User, session: Session, monkeypatch, tmp_path
):
    monkeypatch.setattr(media_store, "media_dir", lambda: tmp_path)
    _install_fakes(monkeypatch)

    events: list[tuple[str, dict]] = []

    async def emit(event: str, data: dict) -> None:
        events.append((event, data))

    await stream_turn(db, user, session, "I go to school", emit)

    event_names = [name for name, _ in events]
    assert event_names[0] == "assistant_started"
    assert event_names.count("assistant_delta") == 2
    assert event_names[-1] == "assistant_response"

    deltas = "".join(data["text"] for name, data in events if name == "assistant_delta")
    assert deltas == "Hello"

    response = events[-1][1]
    assert response["assistant_message"]["text"] == "Hello"
    assert response["assistant_message"]["has_audio"] is True
    assert response["assistant_message"]["audio_url"] is None
    assert response["corrections"][0]["correction"] == "I went"
    assert response["new_vocab"][0]["word"] == "serendipity"

    messages = (
        await db.execute(
            select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
        )
    ).scalars().all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].text == "Hello"
    assert messages[1].audio_url is not None
    assert messages[1].audio_url.startswith("media:")

    corrections = (await db.execute(select(Correction))).scalars().all()
    assert len(corrections) == 1


@pytest.mark.asyncio
async def test_stream_turn_fails_loudly_on_empty_reply(
    db: AsyncSession, user: User, session: Session, monkeypatch
):
    _install_fakes(monkeypatch, chunks=())

    async def emit(event: str, data: dict) -> None:
        pass

    with pytest.raises(RuntimeError, match="empty reply"):
        await stream_turn(db, user, session, "hello", emit)


@pytest.mark.asyncio
async def test_sse_endpoint_frames_events(
    db: AsyncSession, user: User, session: Session, monkeypatch, tmp_path
):
    monkeypatch.setattr(media_store, "media_dir", lambda: tmp_path)
    _install_fakes(monkeypatch)

    response = await stream_message(
        session_id=session.id,
        body=MessageCreate(text="I go to school"),
        current_user=user,
        db=db,
    )

    chunks: list[str] = []
    async for raw in response.body_iterator:
        chunks.append(raw.decode() if isinstance(raw, bytes) else raw)
    body = "".join(chunks)

    assert body.startswith("event: start")
    assert "event: assistant_delta" in body
    assert "event: assistant_response" in body
    assert "Hello" in body
    # Every event frame carries a JSON data line.
    for frame in [f for f in body.split("\n\n") if f.startswith("event:")]:
        assert "\ndata: " in frame
