"""Tests for Iteration 3: Proactive Tutor Greetings, Universal Voice STT, and Custom Scenarios."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import Base, Message, Scenario, Session, User
from app.routers.sessions import (
    create_session,
    upload_recording,
    list_sessions,
    SCENARIO_OPENING_GREETINGS,
)
from app.routers.scenarios import create_scenario
from app.schemas.session import SessionCreate, ScenarioCreate
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.router import LLMRouter


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
    u = User(email="test_conv@example.com", hashed_password="hashed_pw", display_name="ConvStudent")
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_create_session_free_talk_greeting(db: AsyncSession, user: User):
    """Creating a free-talk session generates an initial proactive greeting and sets message_count=1."""
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    res = await create_session(body=body, current_user=user, db=db)

    assert res.message_count == 1
    assert res.scenario_id is None

    # Verify message in database
    msg_res = await db.execute(select(Message).where(Message.session_id == res.id))
    msgs = msg_res.scalars().all()
    assert len(msgs) == 1
    initial_msg = msgs[0]
    assert initial_msg.role == "assistant"
    assert "chat" in initial_msg.text.lower() or "hello" in initial_msg.text.lower()


@pytest.mark.asyncio
async def test_create_session_predefined_scenario_greeting(db: AsyncSession, user: User):
    """Predefined scenarios use their designated in-character opening greeting."""
    scenario = Scenario(
        name="Job Interview",
        system_prompt="You are a hiring manager.",
        cefr_level="B1",
        is_custom=False,
    )
    db.add(scenario)
    await db.flush()

    body = SessionCreate(scenario_id=scenario.id, cefr_level="B1")
    res = await create_session(body=body, current_user=user, db=db)

    assert res.message_count == 1
    assert res.scenario_name == "Job Interview"

    msg_res = await db.execute(select(Message).where(Message.session_id == res.id))
    msgs = msg_res.scalars().all()
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    assert msgs[0].text == SCENARIO_OPENING_GREETINGS["job_interview"]


@pytest.mark.asyncio
async def test_create_session_tts_synthesis_success(monkeypatch, db: AsyncSession, user: User):
    """When TTS succeeds, audio_url is attached to the initial greeting message."""
    async def mock_synthesize(self, text, voice=None):
        return b"fake-audio-bytes-123"

    monkeypatch.setattr(TTSPersonalAPI, "synthesize", mock_synthesize)

    body = SessionCreate(scenario_id=None, cefr_level="B1")
    res = await create_session(body=body, current_user=user, db=db)

    msg_res = await db.execute(select(Message).where(Message.session_id == res.id))
    msgs = msg_res.scalars().all()
    assert len(msgs) == 1
    assert msgs[0].audio_url is not None
    # Audio is stored as a file reference, never as a base64 blob in the row.
    assert msgs[0].audio_url.startswith("media:")
    assert res.initial_message is not None
    assert res.initial_message.has_audio is True
    assert res.initial_message.audio_url is None


@pytest.mark.asyncio
async def test_create_session_tts_synthesis_failure_fallback(monkeypatch, db: AsyncSession, user: User):
    """If TTS synthesis fails or times out, session creation still succeeds and audio_url is None."""
    async def mock_failing_synthesize(self, text, voice=None):
        raise RuntimeError("TTS connection error")

    monkeypatch.setattr(TTSPersonalAPI, "synthesize", mock_failing_synthesize)

    body = SessionCreate(scenario_id=None, cefr_level="B1")
    res = await create_session(body=body, current_user=user, db=db)

    assert res.message_count == 1
    msg_res = await db.execute(select(Message).where(Message.session_id == res.id))
    msgs = msg_res.scalars().all()
    assert len(msgs) == 1
    assert msgs[0].audio_url is None


@pytest.mark.asyncio
async def test_custom_scenario_creation_and_session_greeting(monkeypatch, db: AsyncSession, user: User):
    """Creating a custom scenario and starting a session generates an in-character opening line via LLM or fallback."""
    # Test creating custom scenario
    sc_body = ScenarioCreate(
        name="Hotel Reception Desk",
        description="You are a guest asking about breakfast hours and booking a spa appointment.",
        cefr_level="B1",
    )
    created_sc = await create_scenario(body=sc_body, current_user=user, db=db)
    assert created_sc.is_custom is True
    assert created_sc.name == "Hotel Reception Desk"

    # Branch 1: LLM router succeeds in generating opening greeting
    async def mock_llm_success(self, messages, system_prompt=None, task="conversation", temperature=0.7, max_tokens=80):
        return {"content": "Good evening! Welcome to the Grand Hotel reception. How may I assist you tonight?"}

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", mock_llm_success)

    sess_body = SessionCreate(scenario_id=created_sc.id, cefr_level="B1")
    sess = await create_session(body=sess_body, current_user=user, db=db)
    assert sess.message_count == 1
    assert sess.scenario_name == "Hotel Reception Desk"
    assert sess.initial_message is not None
    assert sess.initial_message.text == "Good evening! Welcome to the Grand Hotel reception. How may I assist you tonight?"

    msg_res = await db.execute(select(Message).where(Message.session_id == sess.id))
    msgs = msg_res.scalars().all()
    assert len(msgs) == 1
    assert msgs[0].text == "Good evening! Welcome to the Grand Hotel reception. How may I assist you tonight?"

    # Branch 2: LLM router fails, fallback opening template is used
    async def mock_llm_fail(self, messages, system_prompt=None, task="conversation", temperature=0.7, max_tokens=80):
        raise RuntimeError("LLM service unavailable")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", mock_llm_fail)

    sess_fallback = await create_session(body=sess_body, current_user=user, db=db)
    assert sess_fallback.message_count == 1
    assert sess_fallback.initial_message is not None
    assert sess_fallback.initial_message.text == "Hello! Welcome to our Hotel Reception Desk session. Whenever you're ready, let's begin!"


@pytest.mark.asyncio
async def test_upload_recording_success(monkeypatch, db: AsyncSession, user: User):
    """POST /api/sessions/{session_id}/recordings transcribes audio and returns transcript & words."""
    # Create active session
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess = await create_session(body=body, current_user=user, db=db)

    # Mock transcribe_audio
    async def mock_transcribe(audio_bytes: bytes, content_type: str = "audio/webm"):
        assert audio_bytes == b"sample-voice-recording-data"
        assert content_type == "audio/webm"
        return {
            "text": "I would like to practice ordering food at a restaurant.",
            "words": [
                {"word": "I", "start": 0.0, "end": 0.2},
                {"word": "would", "start": 0.2, "end": 0.4},
            ],
        }

    import app.routers.sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, "transcribe_audio", mock_transcribe)

    upload = UploadFile(
        file=io.BytesIO(b"sample-voice-recording-data"),
        filename="speech.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )

    resp = await upload_recording(session_id=sess.id, file=upload, current_user=user, db=db)
    assert resp["transcript"] == "I would like to practice ordering food at a restaurant."
    assert len(resp["words"]) == 2
    assert resp["words"][0]["word"] == "I"


@pytest.mark.asyncio
async def test_upload_recording_session_not_found(db: AsyncSession, user: User):
    """upload_recording returns 404 if session does not exist."""
    upload = UploadFile(
        file=io.BytesIO(b"data"),
        filename="speech.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id="non-existent-id", file=upload, current_user=user, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_upload_recording_session_ended(db: AsyncSession, user: User):
    """upload_recording returns 400 if session has ended."""
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess = await create_session(body=body, current_user=user, db=db)

    # End session
    sess_obj = (await db.execute(select(Session).where(Session.id == sess.id))).scalar_one()
    sess_obj.ended_at = datetime.now(timezone.utc)
    await db.flush()

    upload = UploadFile(
        file=io.BytesIO(b"data"),
        filename="speech.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id=sess.id, file=upload, current_user=user, db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_recording_file_too_large(db: AsyncSession, user: User):
    """upload_recording returns 413 if audio file exceeds 10MB."""
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess = await create_session(body=body, current_user=user, db=db)

    # 10MB + 10 bytes
    large_bytes = b"0" * (10 * 1024 * 1024 + 10)
    upload = UploadFile(
        file=io.BytesIO(large_bytes),
        filename="large.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id=sess.id, file=upload, current_user=user, db=db)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_recording_empty_file_raises_400(db: AsyncSession, user: User):
    """upload_recording returns 400 if audio file is empty."""
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess = await create_session(body=body, current_user=user, db=db)

    upload = UploadFile(
        file=io.BytesIO(b""),
        filename="empty.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id=sess.id, file=upload, current_user=user, db=db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_recording_invalid_mime_type_raises_415(db: AsyncSession, user: User):
    """upload_recording returns 415 for non-audio MIME types."""
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess = await create_session(body=body, current_user=user, db=db)

    upload = UploadFile(
        file=io.BytesIO(b"plain-text-data"),
        filename="speech.txt",
        headers=Headers({"content-type": "text/plain"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id=sess.id, file=upload, current_user=user, db=db)
    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_upload_recording_user_isolation_404(db: AsyncSession, user: User):
    """[SEC-004] User B receives 404 when trying to upload recording to User A's session."""
    user_b = User(email="user_b@example.com", hashed_password="hashed_b", display_name="UserB")
    db.add(user_b)
    await db.flush()

    # User A creates a session
    body = SessionCreate(scenario_id=None, cefr_level="B1")
    sess_a = await create_session(body=body, current_user=user, db=db)

    # User B attempts to upload recording to User A's session
    upload = UploadFile(
        file=io.BytesIO(b"valid-audio-data"),
        filename="speech.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await upload_recording(session_id=sess_a.id, file=upload, current_user=user_b, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_session_invalid_scenario_id_raises_404(db: AsyncSession, user: User):
    """create_session returns 404 if scenario_id does not exist."""
    body = SessionCreate(scenario_id="non-existent-uuid-12345", cefr_level="B1")
    with pytest.raises(HTTPException) as exc_info:
        await create_session(body=body, current_user=user, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_session_other_user_custom_scenario_raises_404(db: AsyncSession, user: User):
    """create_session returns 404 if scenario is custom and belongs to another user."""
    other_user = User(email="other_student@example.com", hashed_password="pw", display_name="Other")
    db.add(other_user)
    await db.flush()

    other_scenario = Scenario(
        name="Confidential Negotiation",
        system_prompt="Private scenario context",
        cefr_level="C1",
        is_custom=True,
        user_id=other_user.id,
    )
    db.add(other_scenario)
    await db.flush()

    body = SessionCreate(scenario_id=other_scenario.id, cefr_level="C1")
    with pytest.raises(HTTPException) as exc_info:
        await create_session(body=body, current_user=user, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_sessions_single_query_aggregation(db: AsyncSession, user: User):
    """[REV-001] list_sessions aggregates message_count and scenario_name in a single SQL query."""
    scenario = Scenario(
        name="Job Interview",
        system_prompt="Hiring manager context",
        cefr_level="B2",
        is_custom=False,
    )
    db.add(scenario)
    await db.flush()

    # Create session 1 with scenario
    s1 = await create_session(body=SessionCreate(scenario_id=scenario.id, cefr_level="B2"), current_user=user, db=db)
    # Create session 2 free talk
    s2 = await create_session(body=SessionCreate(scenario_id=None, cefr_level="B1"), current_user=user, db=db)

    results = await list_sessions(current_user=user, db=db)
    assert len(results) == 2

    s1_res = next(s for s in results if s.id == s1.id)
    s2_res = next(s for s in results if s.id == s2.id)

    assert s1_res.scenario_name == "Job Interview"
    assert s1_res.message_count == 1
    assert s2_res.scenario_name is None
    assert s2_res.message_count == 1
