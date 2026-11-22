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


@pytest.mark.asyncio
async def test_start_lesson_conversation_creates_scenario_and_session(db, user, path_with_lesson):
    from app.routers.learning_paths import start_lesson_conversation
    from app.models.models import Session, Scenario, Message

    path, lesson = path_with_lesson
    lesson.lesson_type = "conversation"
    lesson.topic = "Job Interview Negotiations"
    lesson.content = json.dumps({
        "scenario_context": "Negotiating compensation and start date",
        "goal": "Reach an agreement on salary and remote days",
        "tutor_role": "Hiring Manager",
        "target_expressions": ["I'd like to discuss", "Based on my experience"],
    })
    await db.flush()

    res = await start_lesson_conversation(path.id, lesson.id, user, db)
    assert "session_id" in res

    session = (await db.execute(select(Session).where(Session.id == res["session_id"]))).scalar_one()
    assert session.user_id == user.id

    scenario = (await db.execute(select(Scenario).where(Scenario.id == session.scenario_id))).scalar_one()
    assert scenario.is_custom is True
    assert "Job Interview Negotiations" in scenario.name

    messages = (await db.execute(select(Message).where(Message.session_id == session.id))).scalars().all()
    assert len(messages) == 1
    assert messages[0].role == "assistant"
    assert len(messages[0].text) > 0


@pytest.mark.asyncio
async def test_generate_path_uses_curriculum_seed_when_llm_fails(db, user, monkeypatch):
    from app.routers.learning_paths import generate_path, GeneratePathRequest

    async def failing_complete(self, messages, system_prompt, task, **kwargs):
        raise RuntimeError("LLM timed out or connection dropped")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", failing_complete)

    # Path generation must not fail with 503; it must gracefully seed from curriculum templates
    res = await generate_path(GeneratePathRequest(), user, db)
    assert res.current_level == "A1"
    assert res.target_level == "A2"
    assert len(res.path_lessons) > 0
    assert res.lessons_required == len(res.path_lessons)
    assert res.path_lessons[0].topic != ""


@pytest.mark.asyncio
async def test_advance_level_from_a1_to_a2(db, user, monkeypatch):
    """Ensure advance_level advances user level from A1 to A2, updates Setting,
    and generates the next path targeting B1."""
    from app.routers.learning_paths import advance_level
    from app.models.models import Setting

    user.current_level = "A1"
    db.add(Setting(user_id=user.id, key="default_cefr", value="A1"))
    await db.flush()

    path = LearningPath(
        user_id=user.id,
        current_level="A1",
        target_level="A2",
        lessons_required=1,
        lessons_completed=1,
        is_active=True,
    )
    db.add(path)
    await db.flush()

    lesson = PathLesson(
        path_id=path.id,
        lesson_type="grammar",
        topic="Basic Greetings",
        description="Learn hello and goodbye.",
        order=1,
        completed=True,
        content=json.dumps({"focus": "Greetings", "lesson_type": "grammar"}),
    )
    db.add(lesson)
    await db.flush()

    async def failing_complete(self, messages, system_prompt, task, **kwargs):
        raise RuntimeError("Fallback to curriculum seed")

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", failing_complete)

    next_path = await advance_level(path.id, user, db)

    # User level advanced to A2
    assert user.current_level == "A2"

    # default_cefr setting kept in sync
    setting = (
        await db.execute(
            select(Setting).where(Setting.user_id == user.id, Setting.key == "default_cefr")
        )
    ).scalar_one()
    assert setting.value == "A2"

    # Completed path is deactivated
    refreshed_old_path = (
        await db.execute(select(LearningPath).where(LearningPath.id == path.id))
    ).scalar_one()
    assert refreshed_old_path.is_active is False
    assert refreshed_old_path.completed_at is not None

    # Next path targets B1
    assert next_path.current_level == "A2"
    assert next_path.target_level == "B1"
    assert next_path.is_active is True


@pytest.mark.asyncio
async def test_multiple_choice_index_and_letter_answer_resolution(db, user):
    """Ensure _normalize_exercises converts index 0, letter A/B, or 'A) ...'
    to the matching option string, and check_path_lesson_exercise grades flexibly."""
    from app.routers.learning_paths import _normalize_exercises, check_path_lesson_exercise, PathExerciseSubmission

    raw_exercises = [
        {
            "question": "Choose apple",
            "type": "multiple_choice",
            "options": ["Apple", "Banana", "Cherry"],
            "answer": 0,
        },
        {
            "question": "Choose banana",
            "type": "multiple_choice",
            "options": ["Apple", "Banana", "Cherry"],
            "answer": "1",
        },
        {
            "question": "Choose cherry",
            "type": "multiple_choice",
            "options": ["Apple", "Banana", "Cherry"],
            "answer": "C",
        },
        {
            "question": "Choose banana prefixed",
            "type": "multiple_choice",
            "options": ["Apple", "Banana", "Cherry"],
            "answer": "B) Banana",
        },
        {
            "question": "Choose apple option style",
            "type": "multiple_choice",
            "options": ["Apple", "Banana", "Cherry"],
            "answer": "Option A",
        },
    ]

    normalized = _normalize_exercises(raw_exercises)
    assert normalized[0]["answer"] == "Apple"
    assert normalized[1]["answer"] == "Banana"
    assert normalized[2]["answer"] == "Cherry"
    assert normalized[3]["answer"] == "Banana"
    assert normalized[4]["answer"] == "Apple"

    # Test flexible grading in check_path_lesson_exercise
    path = LearningPath(
        user_id=user.id,
        current_level="B1",
        target_level="B2",
        lessons_required=1,
        lessons_completed=0,
        is_active=True,
    )
    db.add(path)
    await db.flush()

    lesson = PathLesson(
        path_id=path.id,
        lesson_type="grammar",
        topic="Flexible Grading Test",
        description="Testing flexible answer comparison.",
        order=1,
        content=json.dumps({
            "focus": "Flexible grading",
            "exercises": [
                {
                    "question": "Fill in the blank: I ___ to work.",
                    "type": "fill_blank",
                    "answer": "walked",
                },
                {
                    "question": "Choose the right word.",
                    "type": "multiple_choice",
                    "options": ["walk", "walked", "walking"],
                    "answer": "walked",
                },
            ],
        }),
    )
    db.add(lesson)
    await db.flush()

    # Trailing period and whitespace
    res1 = await check_path_lesson_exercise(
        path.id, lesson.id, PathExerciseSubmission(exercise_index=0, answer="  walked.  "), user, db
    )
    assert res1["correct"] is True

    # Punctuation quotes and capitalization
    res2 = await check_path_lesson_exercise(
        path.id, lesson.id, PathExerciseSubmission(exercise_index=0, answer='"WALKED"'), user, db
    )
    assert res2["correct"] is True

    # Multiple choice by index
    res3 = await check_path_lesson_exercise(
        path.id, lesson.id, PathExerciseSubmission(exercise_index=1, answer="1"), user, db
    )
    assert res3["correct"] is True

    # Multiple choice by letter with period
    res4 = await check_path_lesson_exercise(
        path.id, lesson.id, PathExerciseSubmission(exercise_index=1, answer="B."), user, db
    )
    assert res4["correct"] is True

    # Multiple choice wrong answer
    res5 = await check_path_lesson_exercise(
        path.id, lesson.id, PathExerciseSubmission(exercise_index=1, answer="0"), user, db
    )
    assert res5["correct"] is False


@pytest.mark.asyncio
async def test_start_lesson_conversation_reuses_existing_scenario(db, user, path_with_lesson):
    """Ensure start_lesson_conversation reuses existing custom scenario
    for the same lesson rather than duplicating orphan scenarios."""
    from app.routers.learning_paths import start_lesson_conversation
    from app.models.models import Scenario, Session

    path, lesson = path_with_lesson
    lesson.lesson_type = "conversation"
    lesson.topic = "At the Airport"
    lesson.content = json.dumps({
        "scenario_context": "Checking in at the airport counter",
        "goal": "Check luggage and get boarding pass",
        "tutor_role": "Airline Staff",
        "target_expressions": ["I have a bag to check", "Window seat please"],
    })
    await db.flush()

    # First launch
    res1 = await start_lesson_conversation(path.id, lesson.id, user, db)
    # Second launch (e.g. user reopens or re-clicks)
    res2 = await start_lesson_conversation(path.id, lesson.id, user, db)

    assert res1["session_id"] != res2["session_id"]

    # Verify only 1 scenario was created
    scenarios = (
        await db.execute(
            select(Scenario).where(
                Scenario.user_id == user.id,
                Scenario.name == f"Lesson: {lesson.topic}",
            )
        )
    ).scalars().all()
    assert len(scenarios) == 1

    # Verify both sessions point to the same scenario
    s1 = (await db.execute(select(Session).where(Session.id == res1["session_id"]))).scalar_one()
    s2 = (await db.execute(select(Session).where(Session.id == res2["session_id"]))).scalar_one()
    assert s1.scenario_id == scenarios[0].id
    assert s2.scenario_id == scenarios[0].id

