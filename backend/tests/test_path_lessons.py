"""Integration tests for the interactive path-lesson endpoints.

Covers:
- POST /{path_id}/lessons/{lesson_id}/generate — lazy, idempotent content
  generation tailored to the path + assessment context, answers stripped.
- POST /{path_id}/lessons/{lesson_id}/exercise — server-side grading; answers
  never leave the server.
- complete_lesson response serialization after core UPDATEs (the
  MissingGreenlet regression: expired identity-map rows + response validation
  require populate_existing on the eager reload).
"""

import json

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.router import LLMRouter
from app.models.models import Base, LearningPath, PathLesson, User
from app.routers.learning_paths import (
    FullLearningPathResponse,
    check_path_lesson_exercise,
    complete_lesson,
    generate_path_lesson,
)


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


@pytest_asyncio.fixture
async def path_with_lesson(db: AsyncSession, user: User) -> tuple[LearningPath, PathLesson]:
    path = LearningPath(
        user_id=user.id, current_level="B1", target_level="B2",
        lessons_required=5, lessons_completed=0, is_active=True,
    )
    db.add(path)
    await db.flush()
    lesson = PathLesson(
        path_id=path.id, lesson_type="grammar", topic="Past tenses",
        description="Narrate experiences accurately.", order=1,
        content=json.dumps({"focus": "Past tenses", "lesson_type": "grammar"}),
    )
    db.add(lesson)
    await db.flush()
    return path, lesson


LESSON_JSON = json.dumps({
    "title": "Past Tenses",
    "explanation": "Use past simple for finished actions.",
    "examples": ["I worked yesterday.", "She studied last night.", "We traveled in 2020."],
    "exercises": [
        {"question": "Fill in the blank: I ___ to school yesterday.", "type": "fill_blank", "answer": "went"},
        {
            "question": "Choose the correct option.",
            "type": "multiple_choice",
            "answer": "have lived",
            "options": ["live", "have lived", "living"],
        },
    ],
})


@pytest.mark.asyncio
async def test_generate_creates_content_and_strips_answers(db, user, path_with_lesson, monkeypatch):
    path, lesson = path_with_lesson
    calls = []

    async def fake_complete(self, messages, system_prompt, task, **kwargs):
        calls.append(system_prompt)
        assert "B1" in messages[0]["content"] and "Past tenses" in messages[0]["content"]
        return {"content": LESSON_JSON, "provider": "test", "model": "test"}

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)

    detail = await generate_path_lesson(path.id, lesson.id, user, db)

    assert detail.content["explanation"] == "Use past simple for finished actions."
    assert len(detail.content["examples"]) == 3
    assert len(detail.content["exercises"]) == 2
    # Answers never leave the server.
    assert all("answer" not in ex for ex in detail.content["exercises"])
    assert all("question" in ex for ex in detail.content["exercises"])

    # Persisted with answers server-side.
    refreshed = (await db.execute(select(PathLesson).where(PathLesson.id == lesson.id))).scalar_one()
    stored = json.loads(refreshed.content)
    assert stored["exercises"][0]["answer"] == "went"


@pytest.mark.asyncio
async def test_generate_is_idempotent(db, user, path_with_lesson, monkeypatch):
    path, lesson = path_with_lesson
    calls = []

    async def fake_complete(self, messages, system_prompt, task, **kwargs):
        calls.append(system_prompt)
        return {"content": LESSON_JSON, "provider": "test", "model": "test"}

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)

    await generate_path_lesson(path.id, lesson.id, user, db)
    detail = await generate_path_lesson(path.id, lesson.id, user, db)

    assert len(calls) == 1  # LLM not invoked again
    assert detail.content["explanation"] == "Use past simple for finished actions."


@pytest.mark.asyncio
async def test_generate_failure_is_503(db, user, path_with_lesson, monkeypatch):
    path, lesson = path_with_lesson

    async def failing_complete(self, messages, system_prompt, task, **kwargs):
        raise RuntimeError("All providers failed")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", failing_complete)

    with pytest.raises(HTTPException) as exc:
        await generate_path_lesson(path.id, lesson.id, user, db)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_exercise_grading_server_side(db, user, path_with_lesson, monkeypatch):
    path, lesson = path_with_lesson

    async def fake_complete(self, messages, system_prompt, task, **kwargs):
        return {"content": LESSON_JSON, "provider": "test", "model": "test"}

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)
    await generate_path_lesson(path.id, lesson.id, user, db)

    from app.routers.learning_paths import PathExerciseSubmission

    ok = await check_path_lesson_exercise(path.id, lesson.id, PathExerciseSubmission(exercise_index=0, answer="  WENT "), user, db)
    assert ok["correct"] is True
    assert ok["correct_answer"] == "went"

    bad = await check_path_lesson_exercise(path.id, lesson.id, PathExerciseSubmission(exercise_index=0, answer="go"), user, db)
    assert bad["correct"] is False
    assert bad["correct_answer"] == "went"

    with pytest.raises(HTTPException) as exc:
        await check_path_lesson_exercise(path.id, lesson.id, PathExerciseSubmission(exercise_index=99, answer="x"), user, db)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_complete_lesson_response_validates_after_core_updates(db, user, path_with_lesson):
    """Regression: complete_lesson runs core UPDATEs (bypassing the identity
    map) and then re-serializes the path. Without populate_existing on the
    reload, response validation touches expired attributes and raises
    MissingGreenlet."""
    from app.routers.learning_paths import CompleteLessonRequest

    path, lesson = path_with_lesson
    result = await complete_lesson(
        path.id, lesson.id, CompleteLessonRequest(completed=True), user, db,
    )
    served = FullLearningPathResponse.model_validate(result)
    assert served.lessons_completed == 1
    assert served.path_lessons[0].completed is True
