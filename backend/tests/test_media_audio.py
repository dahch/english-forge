"""Audio storage: files instead of base64 blobs, served with auth."""

from __future__ import annotations

import base64
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.router import LLMRouter
from app.models.models import Base, Message, Session, User
from app.routers.messages import get_message_audio, send_message
from app.schemas.session import MessageCreate
from app.services import media_store


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
    u = User(email="audio@example.com", hashed_password="x", display_name="Audio")
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_media_store_roundtrip_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "media_dir", lambda: tmp_path)

    reference = media_store.store_audio(b"hello-audio")
    assert reference.startswith("media:")
    assert media_store.read_audio(reference) == b"hello-audio"
    assert media_store.has_audio(reference) is True

    # Path traversal / arbitrary keys are rejected.
    assert media_store.read_audio("media:../../etc/passwd") is None
    assert media_store.read_audio("media:deadbeef.mp3") is None

    # Legacy data URIs remain readable.
    legacy = "data:audio/mpeg;base64," + base64.b64encode(b"old").decode()
    assert media_store.read_audio(legacy) == b"old"


@pytest.mark.asyncio
async def test_turn_stores_audio_as_file_and_serves_it(
    db: AsyncSession, user: User, monkeypatch, tmp_path
):
    monkeypatch.setattr(media_store, "media_dir", lambda: tmp_path)

    async def fake_complete(self, messages, system_prompt, task="conversation", **kwargs):
        return {
            "content": json.dumps({"reply": "Hello there!", "corrections": [], "new_vocab": []}),
            "provider": "mock",
            "model": "mock",
        }

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)

    async def fake_tts(self, text, voice=None):
        return b"mp3-bytes"

    monkeypatch.setattr(TTSPersonalAPI, "synthesize", fake_tts)

    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()

    turn = await send_message(
        session_id=session.id,
        body=MessageCreate(text="Hi"),
        current_user=user,
        db=db,
    )

    assert turn.assistant_message.audio_url is None  # never a data URI
    assert turn.assistant_message.has_audio is True

    stored = (
        await db.execute(select(Message).where(Message.id == turn.assistant_message.id))
    ).scalar_one()
    assert stored.audio_url is not None
    assert stored.audio_url.startswith("media:")
    assert "base64" not in stored.audio_url

    response = await get_message_audio(
        session_id=session.id,
        message_id=stored.id,
        current_user=user,
        db=db,
    )
    assert response.body == b"mp3-bytes"
    assert response.media_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_legacy_data_uri_is_still_served(db: AsyncSession, user: User):
    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()
    legacy = "data:audio/mpeg;base64," + base64.b64encode(b"legacy").decode()
    message = Message(session_id=session.id, role="assistant", text="old", audio_url=legacy)
    db.add(message)
    await db.flush()

    response = await get_message_audio(
        session_id=session.id,
        message_id=message.id,
        current_user=user,
        db=db,
    )
    assert response.body == b"legacy"
