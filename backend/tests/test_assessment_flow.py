"""Integration test for the multi-phase assessment flow.

Drives the real router handlers against an in-memory SQLite DB, with the
LLM (conversation, listening grading, analysis) and TTS mocked. Covers:
- mic check → conversation transition
- the MIN_ASSESSMENT_EXCHANGES gate (LLM cannot end the interview early)
- conversation → listening → speaking phase transitions
- is_complete only after the speaking bank is exhausted
- deterministic aggregation in _analyze_assessment
"""

import json

import pytest
import pytest_asyncio
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.router import LLMRouter
from app.models.models import Assessment, Base, AssessmentMessage, User
from app.routers.assessment import (
    AssessmentMessageCreate,
    AssessmentResponse,
    assessment_message,
    complete_assessment,
    start_assessment,
)
from app.services.assessment_flow import MAX_ASSESSMENT_EXCHANGES, MIN_ASSESSMENT_EXCHANGES


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
    u = User(email="test@example.com", hashed_password="x", display_name="Test")
    db.add(u)
    await db.flush()
    return u


def _install_llm(monkeypatch, *, listening_correct: bool = True):
    """Fake LLMRouter.complete_with_fallback that branches on the prompt."""
    calls = []

    async def fake_complete(self, messages, system_prompt, task, **kwargs):
        calls.append(system_prompt)
        last_user = messages[-1]["content"] if messages else ""
        if "listening comprehension" in system_prompt:
            reply = {"correct": listening_correct, "reason": "ok"}
        elif "OBJECTIVELY MEASURED" in last_user:
            reply = {"grammar": 60, "vocabulary": 55, "fluency": 50,
                     "recommendations": ["practice more"], "summary": "Nivel B1."}
        else:  # conversation
            reply = {"reply": "And why is that?", "question_count": 2, "is_complete": True}
        return {"content": json.dumps(reply), "provider": "test", "model": "test"}

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)
    return calls


async def _send(db, user, assessment_id, text, source="voice", words=None, item_id=None):
    return await assessment_message(
        assessment_id, AssessmentMessageCreate(text=text, source=source, words=words, item_id=item_id), user, db
    )


async def _assistant_kinds(db, assessment_id):
    result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    return [m.kind for m in result.scalars() if m.role == "assistant"]


@pytest.mark.asyncio
async def test_full_flow(db, user, monkeypatch):
    _install_llm(monkeypatch)

    resp = await start_assessment(Response(), user, db)
    assert resp.phase == "mic_check"
    assert resp.messages[-1].kind == "mic_check"

    # Mic check answer → conversation begins with question 1.
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog", source="voice")
    assert resp.phase == "conversation"
    assert resp.messages[-1].kind == "chat"

    # The LLM always answers is_complete=True, but the server must ignore it
    # until MIN_ASSESSMENT_EXCHANGES questions have been asked.
    for i in range(MIN_ASSESSMENT_EXCHANGES - 1):
        resp = await _send(db, user, resp.id, f"Answer number {i}")
        assert resp.phase == "conversation", f"premature phase exit at exchange {i + 1}"

    # Exchange MIN reached — the LLM's is_complete is now honored, and the
    # flow advances to the listening section instead of ending the assessment.
    resp = await _send(db, user, resp.id, "Answer that closes the interview")
    assert resp.phase == "listening"
    assert resp.is_complete is False
    assert resp.messages[-1].kind == "listening"
    kinds = await _assistant_kinds(db, resp.id)
    assert kinds[-1] == "listening"

    # Answer the whole listening bank correctly → speaking section.
    from app.services.assessment_bank import LISTENING_ITEMS, SPEAKING_ITEMS

    for i in range(len(LISTENING_ITEMS)):
        resp = await _send(db, user, resp.id, f"listening answer {i}")
        if i < len(LISTENING_ITEMS) - 1:
            assert resp.phase == "listening"
            assert resp.messages[-1].kind == "listening"
    assert resp.phase == "speaking"
    assert resp.messages[-1].kind == "speaking"

    # Pronunciation items carry the expected sentence + focus in the text.
    assert "Focus:" in resp.messages[-1].text

    # Answer the whole speaking bank with the exact item text — the server
    # must recompute the composite itself from (item text, transcript, words);
    # there is no client-injected score anymore.
    for i in range(len(SPEAKING_ITEMS)):
        item = SPEAKING_ITEMS[i]
        resp = await _send(db, user, resp.id, item.text, source="voice", words=[])
        if i < len(SPEAKING_ITEMS) - 1:
            assert resp.phase == "speaking"
            assert resp.is_complete is False

    # Bank exhausted → is_complete signals the client to call /complete.
    assert resp.is_complete is True

    result = await complete_assessment(resp.id, user, db)
    # FastAPI would validate through the response model when served over
    # HTTP; direct handler calls must do the same to see parsed JSON fields.
    served = AssessmentResponse.model_validate(result)
    assert result.completed_at is not None
    assert served.dimension_scores is not None
    assert served.dimension_scores["listening"] == 100.0
    # Perfect read-aloud transcripts → composite 100 across the bank.
    assert served.dimension_scores["pronunciation"] == 100.0
    assert served.estimated_level is not None
    assert 0.05 <= served.confidence <= 0.95
    # Strengths/weaknesses are labels of measured dimensions only.
    assert set(served.strengths) <= {"grammar", "vocabulary", "fluency", "listening", "pronunciation"}


@pytest.mark.asyncio
async def test_wants_to_finish_is_respected_before_min(db, user, monkeypatch):
    """Explicit user intent (end word) still ends the interview early — the
    min-exchange gate only blocks the LLM's own decision."""
    _install_llm(monkeypatch)
    resp = await start_assessment(Response(), user, db)
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog")
    assert resp.phase == "conversation"

    resp = await _send(db, user, resp.id, "stop")
    assert resp.phase == "listening"


@pytest.mark.asyncio
async def test_max_cap_forces_transition(db, user, monkeypatch):
    _install_llm(monkeypatch)
    resp = await start_assessment(Response(), user, db)
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog")
    for _ in range(MAX_ASSESSMENT_EXCHANGES):
        if resp.phase != "conversation":
            break
        resp = await _send(db, user, resp.id, "some answer")
    assert resp.phase == "listening"


@pytest.mark.asyncio
async def test_voice_required_in_voice_measured_phases(db, user, monkeypatch):
    """Typed answers are rejected in mic_check/speaking — typing would bypass
    the STT/pronunciation measurement. The client hides the keyboard; this is
    the server-side backstop."""
    from fastapi import HTTPException

    _install_llm(monkeypatch)
    resp = await start_assessment(Response(), user, db)
    assert resp.phase == "mic_check"

    with pytest.raises(HTTPException) as exc:
        await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog", source="text")
    assert exc.value.status_code == 400

    # A voice answer still works and moves the interview forward.
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog")
    assert resp.phase == "conversation"


@pytest.mark.asyncio
async def test_duplicate_item_submission_does_not_advance(db, user, monkeypatch):
    """Sending two answers for the same banked item (double-click) must not
    double-advance the section or corrupt the message history."""
    from app.services.assessment_bank import LISTENING_ITEMS

    _install_llm(monkeypatch)
    resp = await start_assessment(Response(), user, db)
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog")
    # Reach the listening section (explicit finish wins before the min gate).
    resp = await _send(db, user, resp.id, "stop")
    assert resp.phase == "listening"
    first_item = LISTENING_ITEMS[0]
    second_item = LISTENING_ITEMS[1]

    # Answer item 1 (client declares item_id, as the frontend does).
    first = await _send(db, user, resp.id, "answer one", item_id=first_item.id)
    assert first.phase == "listening"
    assert first.messages[-1].kind == "listening"

    # Sequential double-click: the second submission still references item 1
    # while item 2 is current — it must be dropped, not treated as item 2's
    # answer, and the history must not grow.
    dup = await _send(db, user, resp.id, "answer one again", item_id=first_item.id)
    assert dup.phase == "listening"
    assert dup.messages[-1].text == second_item.tutor_text

    user_listening = [m for m in dup.messages if m.role == "user" and m.kind == "listening"]
    assert len(user_listening) == 1

    # Answering the actual current item still works and advances.
    ok = await _send(db, user, resp.id, "answer two", item_id=second_item.id)
    assert ok.phase == "listening"
    assert ok.messages[-1].kind == "listening"
    assert ok.messages[-1].text == LISTENING_ITEMS[2].tutor_text


@pytest.mark.asyncio
async def test_analysis_falls_back_when_llm_fails(db, user, monkeypatch):
    """When every LLM attempt fails (e.g. a reasoning model returning empty
    content), the analysis must still complete deterministically from the
    measured evidence instead of raising RuntimeError — /complete can never
    fail with a 503 for an LLM outage."""
    from app.services.assessment_flow import analyze_assessment, reload_assessment

    async def failing_complete(self, *args, **kwargs):
        raise RuntimeError("All providers failed. Last error: Provider Firework AI returned empty content")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", failing_complete)

    assessment = Assessment(user_id=user.id, phase="speaking", section_step=0)
    db.add(assessment)
    await db.flush()
    db.add(AssessmentMessage(assessment_id=assessment.id, role="assistant", text="Hello!", kind="chat"))
    db.add(AssessmentMessage(
        assessment_id=assessment.id, role="user", text="Because of the rain", kind="listening",
        metrics=json.dumps({"item_id": "l1", "correct": 1, "source": "voice"}),
    ))
    await db.flush()

    await analyze_assessment(db, user, assessment)

    served = AssessmentResponse.model_validate(await reload_assessment(db, assessment.id))
    assert served.estimated_level is not None
    assert served.summary  # fallback summary present, never blank
    assert served.dimension_scores["listening"] == 100.0
    # LLM-scored dimensions stay unmeasured — never invented.
    assert served.dimension_scores["grammar"] is None
    assert served.recommendations  # fallback recommendation points to re-analyze


@pytest.mark.asyncio
async def test_pronunciation_metrics_always_recomputed(db, user, monkeypatch):
    """Client-supplied scores are impossible: the composite is derived from
    (item text, transcript, words) server-side. A garbage transcript with
    fabricated 'perfect' data still scores low."""
    from app.services.assessment_bank import LISTENING_ITEMS, SPEAKING_ITEMS

    _install_llm(monkeypatch)
    resp = await start_assessment(Response(), user, db)
    resp = await _send(db, user, resp.id, "The quick brown fox jumps over the lazy dog")
    resp = await _send(db, user, resp.id, "stop")
    # Answer all listening items correctly to reach the speaking section.
    for i in range(len(LISTENING_ITEMS)):
        resp = await _send(db, user, resp.id, f"listening answer {i}")
    assert resp.phase == "speaking"

    item = SPEAKING_ITEMS[0]
    resp = await _send(
        db, user, resp.id, "completely unrelated words", source="voice",
        words=[{"word": "completely", "start": 0.0, "end": 0.5}, {"word": "unrelated", "start": 0.6, "end": 1.0}],
    )
    # The stored metrics must reflect the REAL match, not anything the client
    # claimed (there is no way to claim anything anymore).
    result = await db.execute(
        select(AssessmentMessage).where(
            AssessmentMessage.assessment_id == resp.id,
            AssessmentMessage.role == "user",
            AssessmentMessage.kind == "speaking",
        )
    )
    stored = result.scalars().one()
    payload = json.loads(stored.metrics)
    assert payload["item_id"] == item.id
    assert payload["composite"] < 50
    assert payload["word_accuracy"] < 0.5
