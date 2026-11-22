"""Contracts the UI relies on for honest feedback.

The frontend must never infer progress or provenance on its own:
- assessment responses carry section/step/question counts,
- generated lessons say whether the LLM or the deterministic fallback produced
  them,
- generated learning paths say whether the curriculum seed was used.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.router import LLMRouter
from app.models.models import Base, LearningPath, User
from app.routers.assessment import (
    AssessmentMessageCreate,
    assessment_message,
    start_assessment,
)
from app.routers.learning_paths import (
    FullLearningPathResponse,
    _generate_path_for_user,
)
from app.routers.lessons import LessonGenerateRequest, generate_lesson


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
    u = User(
        email="contracts@example.com",
        hashed_password="x",
        display_name="Contracts",
        current_level="A1",
    )
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_assessment_response_exposes_progress(db: AsyncSession, user: User, monkeypatch):
    async def fake_complete(self, messages, system_prompt, task="assessment", **kwargs):
        return {
            "content": json.dumps({"reply": "What do you do for fun?", "is_complete": False}),
            "provider": "mock",
            "model": "mock",
        }

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)

    started = await start_assessment(response=Response(), current_user=user, db=db)
    assert started.phase == "mic_check"
    assert started.section_step == 0
    assert started.section_total == 1
    assert started.questions_asked == 0

    after_mic = await assessment_message(
        assessment_id=started.id,
        body=AssessmentMessageCreate(text="The quick brown fox", source="voice"),
        current_user=user,
        db=db,
    )
    assert after_mic.phase == "conversation"
    assert after_mic.section_total == 10
    assert after_mic.questions_asked == 1
    assert after_mic.section_step == 0


@pytest.mark.asyncio
async def test_lesson_generation_reports_source(db: AsyncSession, user: User, monkeypatch):
    async def llm_down(self, messages, system_prompt, task="lesson", **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", llm_down)
    fallback = await generate_lesson(
        body=LessonGenerateRequest(level="A1"), current_user=user, db=db
    )
    assert fallback["source"] == "fallback"
    assert fallback["exercises"]

    async def llm_ok(self, messages, system_prompt, task="lesson", **kwargs):
        return {
            "content": json.dumps(
                {
                    "title": "Custom lesson",
                    "topic": "grammar",
                    "level": "A1",
                    "explanation": "A clear explanation.",
                    "examples": ["Example one.", "Example two.", "Example three."],
                    "exercises": [
                        {
                            "question": "Fill in the blank: She ___ here.",
                            "type": "fill_blank",
                            "answer": "works",
                            "options": ["work", "works"],
                        }
                    ],
                }
            ),
            "provider": "mock",
            "model": "mock",
        }

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", llm_ok)
    generated = await generate_lesson(
        body=LessonGenerateRequest(level="A1"), current_user=user, db=db
    )
    assert generated["source"] == "llm"
    assert generated["title"] == "Custom lesson"


@pytest.mark.asyncio
async def test_path_reports_curriculum_fallback(db: AsyncSession, user: User, monkeypatch):
    async def llm_down(self, messages, system_prompt, task="lesson", **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", llm_down)
    path = await _generate_path_for_user(db, user)
    assert path.generation_source == "curriculum"
    assert path.path_lessons

    # An ORM instance that was never through the generator has no transient
    # attribute: the response schema must fall back to "unknown" instead of
    # failing validation (/current returns such objects).
    bare = LearningPath(
        id="bare-path",
        current_level="A1",
        target_level="A2",
        lessons_required=0,
        lessons_completed=0,
        created_at=datetime.now(timezone.utc),
        completed_at=None,
        is_active=True,
    )
    bare.path_lessons = []
    assert not hasattr(bare, "generation_source")
    serialized = FullLearningPathResponse.model_validate(bare)
    assert serialized.generation_source == "unknown"
