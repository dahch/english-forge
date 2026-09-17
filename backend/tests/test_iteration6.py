"""Tests for Iteration 6 — CEFR Level Lifecycle, Assessment Completion Resilience & Settings Fixes."""

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.router import LLMRouter
from app.models.models import (
    Assessment,
    AssessmentMessage,
    Base,
    LearningPath,
    Setting,
    User,
)
from app.routers.assessment import (
    complete_assessment,
    start_assessment,
)
from app.routers.settings import clear_user_data, update_settings
from app.schemas.settings import UserSettingsUpdate
from app.services.assessment_bank import LISTENING_ITEMS, SPEAKING_ITEMS
from app.services.assessment_flow import analyze_assessment, reload_assessment


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
    u = User(email="iteration6@example.com", hashed_password="pw", display_name="Iter6User")
    db.add(u)
    await db.flush()
    return u


def _fake_llm(monkeypatch):
    """Install mock LLM completing assessment analysis and path generation."""
    async def fake_complete(self, messages, system_prompt, task, **kwargs):
        content = messages[-1]["content"] if messages else ""
        if "CONVERSATION:" in content or task == "assessment":
            return {
                "content": json.dumps({
                    "summary": "Demonstrated strong conversational flow.",
                    "strengths": ["Clear pronunciation", "Varied vocabulary"],
                    "weaknesses": ["Past tense consistency"],
                    "recommendations": ["Practice conditional clauses"],
                    "grammar": 75,
                    "vocabulary": 70,
                    "fluency": 72,
                }),
                "provider": "mock",
                "model": "mock-model",
            }
        # Learning path generation fallback
        return {
            "content": json.dumps({
                "lessons": [
                    {"lesson_type": "grammar", "topic": "Past Perfect", "description": "Learn past perfect"},
                    {"lesson_type": "vocabulary", "topic": "Workplace Phrasals", "description": "Professional idioms"},
                ]
            }),
            "provider": "mock",
            "model": "mock-model",
        }

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)


@pytest.mark.asyncio
async def test_user_default_level_is_a1(db):
    """User.current_level default must be A1."""
    new_user = User(email="new_default@example.com", hashed_password="pw")
    db.add(new_user)
    await db.flush()
    assert new_user.current_level == "A1"
    assert new_user.assessment_completed is False


@pytest.mark.asyncio
async def test_analyze_assessment_upserts_default_cefr_setting(db, user, monkeypatch):
    """analyze_assessment sets user.current_level, assessment_completed, and Setting(key='default_cefr')."""
    _fake_llm(monkeypatch)

    assessment = Assessment(user_id=user.id, phase="speaking", section_step=0)
    db.add(assessment)
    await db.flush()

    db.add(AssessmentMessage(assessment_id=assessment.id, role="assistant", text="Hello!", kind="chat"))
    db.add(AssessmentMessage(assessment_id=assessment.id, role="user", text="I love reading books in English.", kind="chat"))
    await db.flush()

    await analyze_assessment(db, user, assessment)

    assert assessment.estimated_level is not None
    assert user.current_level == assessment.estimated_level
    assert user.assessment_completed is True

    # Check that Setting(key="default_cefr") was upserted
    res = await db.execute(
        select(Setting).where(Setting.user_id == user.id, Setting.key == "default_cefr")
    )
    setting = res.scalar_one_or_none()
    assert setting is not None
    assert setting.value == assessment.estimated_level


@pytest.mark.asyncio
async def test_complete_assessment_explicit_commit_and_idempotent(db, user, monkeypatch):
    """complete_assessment explicitly commits and handles repeated re-calls idempotently."""
    _fake_llm(monkeypatch)

    assessment = Assessment(user_id=user.id, phase="conversation", section_step=0)
    db.add(assessment)
    await db.flush()

    db.add(AssessmentMessage(assessment_id=assessment.id, role="assistant", text="Tell me about your job.", kind="chat"))
    db.add(AssessmentMessage(assessment_id=assessment.id, role="user", text="I work as a software developer.", kind="chat"))
    await db.flush()

    resp1 = await complete_assessment(assessment.id, user, db)
    assert resp1.id == assessment.id
    assert resp1.completed_at is not None

    # Idempotent second call
    resp2 = await complete_assessment(assessment.id, user, db)
    assert resp2.id == assessment.id
    assert resp2.completed_at == resp1.completed_at
    assert resp2.estimated_level == resp1.estimated_level


@pytest.mark.asyncio
async def test_update_settings_level_and_regenerate_path(db, user, monkeypatch):
    """Updating default_cefr updates current_user.current_level and regenerates path when regenerate_path=True."""
    _fake_llm(monkeypatch)

    # Initial level is A1
    assert user.current_level == "A1"

    # Update settings with default_cefr="B1" and regenerate_path=True
    update_body = UserSettingsUpdate(
        default_cefr="B1",
        regenerate_path=True,
    )
    await update_settings(update_body, user, db)

    assert user.current_level == "B1"

    # Active learning path should now exist for B1 -> B2
    res = await db.execute(
        select(LearningPath).where(LearningPath.user_id == user.id, LearningPath.is_active == True)
    )
    active_path = res.scalar_one_or_none()
    assert active_path is not None
    assert active_path.current_level == "B1"
    assert active_path.target_level == "B2"


@pytest.mark.asyncio
async def test_clear_user_data_resets_to_a1(db, user):
    """clear_user_data resets user level to A1 and assessment_completed to False."""
    user.current_level = "C1"
    user.assessment_completed = True
    await db.flush()

    result = await clear_user_data(user, db)
    assert result["message"] == "All user data cleared successfully"
    assert user.current_level == "A1"
    assert user.assessment_completed is False


def test_user_settings_update_invalid_cefr_rejected():
    """Pydantic must reject invalid CEFR levels (SEC-001)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserSettingsUpdate(default_cefr="INVALID")

    with pytest.raises(ValidationError):
        UserSettingsUpdate(default_cefr="b1")  # lowercase not in Literal["A1", "A2", "B1", "B2", "C1", "C2"]

    # Valid CEFR passes
    valid = UserSettingsUpdate(default_cefr="B2")
    assert valid.default_cefr == "B2"

