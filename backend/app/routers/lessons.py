from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.llm.router import parse_lesson_json
from app.models.models import Correction, GeneratedLesson, Message, Session, User
from app.utils import bump_daily_lessons

router = APIRouter(prefix="/api/lessons", tags=["lessons"])

LESSONS_DIR = Path(__file__).parent.parent / "lessons" / "data"


def _load_static_lessons() -> list[dict]:
    lessons = []
    if not LESSONS_DIR.exists():
        return lessons
    for f in sorted(LESSONS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data["id"] = f.stem
            lessons.append(data)
        except Exception:
            continue
    return lessons


def _strip_answers(lesson: dict) -> dict:
    """Answers never leave the server — grading happens via the check endpoint."""
    sanitized = {k: v for k, v in lesson.items() if k != "exercises"}
    sanitized["exercises"] = [
        {k: v for k, v in ex.items() if k != "answer"}
        for ex in lesson.get("exercises", [])
    ]
    return sanitized


def normalize_llm_lesson(lesson_data: dict) -> dict:
    """Normalize LLM output — coerce strings/dicts into the expected shapes.

    The LLM can return double-encoded JSON strings, non-lists where lists are
    expected, and exercises missing keys. Coerce what's usable and drop the
    rest so we never persist an unusable lesson.
    """
    def _as_str(value) -> str | None:
        return str(value) if value else None

    def _as_list(value) -> list:
        # The LLM sometimes double-encodes lists as JSON strings.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [value] if value else []
        return value if isinstance(value, list) else [value] if value else []

    examples = [str(ex) for ex in _as_list(lesson_data.get("examples", []))]

    normalized_exercises: list[dict] = []
    for ex in _as_list(lesson_data.get("exercises", [])):
        if isinstance(ex, str):
            try:
                ex = json.loads(ex)
            except Exception:
                continue
        if not isinstance(ex, dict):
            continue
        question = str(ex.get("question", "")).strip()
        if not question:
            continue
        ex["question"] = question
        ex["answer"] = str(ex.get("answer", ""))
        normalized_exercises.append(ex)

    return {
        "title": _as_str(lesson_data.get("title")) or "Personalized Lesson",
        "topic": _as_str(lesson_data.get("topic")),
        "level": _as_str(lesson_data.get("level")),
        "explanation": str(lesson_data.get("explanation", "")),
        "examples": examples,
        "exercises": normalized_exercises,
    }


def _serialize_generated_lesson(lesson: GeneratedLesson) -> dict:
    """Answer-stripped serialization — the contract shared by the list and
    generate endpoints (ADR-003/005). The frontend discriminates generated
    lessons by the presence of `completed`, so every serialized lesson
    carries it."""
    return {
        "id": lesson.id,
        "title": lesson.title,
        "topic": lesson.topic,
        "level": lesson.level,
        "explanation": lesson.explanation,
        "examples": json.loads(lesson.examples) if lesson.examples else [],
        # Answers stripped — grading happens via the exercise endpoint.
        "exercises": [
            {k: v for k, v in ex.items() if k != "answer"}
            for ex in (json.loads(lesson.exercises) if lesson.exercises else [])
        ],
        "based_on_errors": lesson.based_on_errors,
        "completed": lesson.completed,
        "completed_at": lesson.completed_at.isoformat() if lesson.completed_at else None,
        "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
    }


@router.get("/library")
async def list_library(
    current_user: User = Depends(get_current_user),
):
    return [
        _strip_answers(lesson) | {"exercise_count": len(lesson.get("exercises", []))}
        for lesson in _load_static_lessons()
    ]


@router.get("/library/{lesson_id}")
async def get_lesson(
    lesson_id: str,
    current_user: User = Depends(get_current_user),
):
    for lesson in _load_static_lessons():
        if lesson["id"] == lesson_id:
            return _strip_answers(lesson)
    raise HTTPException(status_code=404, detail="Lesson not found")


class ExerciseSubmission(BaseModel):
    exercise_index: int
    answer: str


@router.post("/library/{lesson_id}/exercise")
async def check_exercise(
    lesson_id: str,
    body: ExerciseSubmission,
    current_user: User = Depends(get_current_user),
):
    lesson = None
    for l in _load_static_lessons():
        if l["id"] == lesson_id:
            lesson = l
            break
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    exercises = lesson.get("exercises", [])
    if body.exercise_index < 0 or body.exercise_index >= len(exercises):
        raise HTTPException(status_code=400, detail="Invalid exercise index")

    exercise = exercises[body.exercise_index]
    correct_answer = exercise.get("answer", "")
    is_correct = body.answer.strip().lower() == correct_answer.strip().lower()

    return {
        "correct": is_correct,
        "correct_answer": correct_answer,
        "explanation": exercise.get("explanation", ""),
    }


@router.get("/generated")
async def list_generated_lessons(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeneratedLesson)
        .where(GeneratedLesson.user_id == current_user.id)
        .order_by(GeneratedLesson.created_at.desc())
    )
    lessons = result.scalars().all()
    return [_serialize_generated_lesson(l) for l in lessons]


class GeneratedLessonComplete(BaseModel):
    completed: bool = True


@router.post("/generated/{lesson_id}/exercise")
async def check_generated_exercise(
    lesson_id: str,
    body: ExerciseSubmission,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeneratedLesson).where(
            GeneratedLesson.id == lesson_id, GeneratedLesson.user_id == current_user.id
        )
    )
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    exercises = json.loads(lesson.exercises) if lesson.exercises else []
    if body.exercise_index < 0 or body.exercise_index >= len(exercises):
        raise HTTPException(status_code=400, detail="Invalid exercise index")

    exercise = exercises[body.exercise_index]
    correct_answer = str(exercise.get("answer", ""))
    is_correct = body.answer.strip().lower() == correct_answer.strip().lower()

    return {
        "correct": is_correct,
        "correct_answer": correct_answer,
        "explanation": exercise.get("explanation", ""),
    }


@router.patch("/generated/{lesson_id}")
async def complete_generated_lesson(
    lesson_id: str,
    body: GeneratedLessonComplete,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(GeneratedLesson)
        .where(GeneratedLesson.id == lesson_id, GeneratedLesson.user_id == current_user.id)
    )
    lesson = result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    # Atomic state transition — the WHERE clause rejects rows already in the
    # requested state, so concurrent/double-clicked requests can't double-count.
    transition = await db.execute(
        update(GeneratedLesson)
        .where(
            GeneratedLesson.id == lesson_id,
            GeneratedLesson.completed == (not body.completed),
        )
        .values(
            completed=body.completed,
            completed_at=datetime.now(timezone.utc) if body.completed else None,
        )
    )

    if transition.rowcount:
        await bump_daily_lessons(db, current_user.id, +1 if body.completed else -1)

    await db.flush()
    # refresh (not expire) — expired attribute access triggers synchronous IO
    # in an AsyncSession (MissingGreenlet). refresh reloads within async context.
    await db.refresh(lesson)

    return {
        "id": lesson.id,
        "completed": lesson.completed,
        "completed_at": lesson.completed_at.isoformat() if lesson.completed_at else None,
    }


@router.post("/generate")
async def generate_lesson(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    corr_result = await db.execute(
        select(Correction.error_type, func.count(Correction.id).label("cnt"))
        .join(Message, Correction.message_id == Message.id)
        .join(Session, Message.session_id == Session.id)
        .where(Session.user_id == current_user.id)
        .group_by(Correction.error_type)
        .order_by(func.count(Correction.id).desc())
        .limit(3)
    )
    top_errors = corr_result.all()

    if not top_errors:
        raise HTTPException(status_code=400, detail="No correction data yet. Practice more to get personalized lessons.")

    error_summary = ", ".join(f"{err_type} ({count} times)" for err_type, count in top_errors)

    from app.llm.router import LLMRouter
    llm = LLMRouter(db, current_user.id)

    prompt = (
        f"Create a short English lesson (JSON) focused on the student's most frequent errors: {error_summary}.\n"
        f"Return JSON with this EXACT structure (do not omit any field):\n"
        f'{{"title": "Lesson title here", "topic": "grammar|vocabulary|naturalness", "level": "A1-C2", "explanation": "A clear 2-3 sentence explanation of the concept", "examples": ["Example 1 showing the correct usage", "Example 2 showing the correct usage", "Example 3 showing the correct usage"], '
        f'"exercises": [{{"question": "Fill in the blank: ...", "type": "fill_blank", "answer": "correct answer", "options": ["option1", "option2", "option3"]}}]}}\n'
        f"CRITICAL: You MUST include 3-5 exercises with questions, answers, and options. Do NOT return empty arrays. Respond with ONLY valid JSON."
    )

    lesson_data = None
    for attempt in range(3):
        try:
            result = await llm.complete_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are an English language teacher. Create focused, practical lessons with exercises. Respond in valid JSON only. Never return empty arrays for exercises.",
                task="lesson",
                temperature=0.5,
                max_tokens=1500,
            )
            lesson_data = parse_lesson_json(result["content"])
            if lesson_data.get("explanation") or lesson_data.get("exercises"):
                break
            logger.warning(f"Lesson generation attempt {attempt + 1}/3 returned empty content")
            lesson_data = None
        except Exception as e:
            logger.error(f"Lesson generation attempt {attempt + 1}/3 failed: {e}")
            lesson_data = None

    if not lesson_data:
        raise HTTPException(status_code=503, detail="Failed to generate lesson after multiple attempts. Please try again.")

    normalized = normalize_llm_lesson(lesson_data)
    if not normalized["exercises"]:
        raise HTTPException(
            status_code=503,
            detail="LLM returned a lesson without valid exercises. Try again.",
        )

    # Persist the generated lesson so it survives navigation/reloads
    stored = GeneratedLesson(
        user_id=current_user.id,
        title=normalized["title"],
        topic=normalized["topic"],
        level=normalized["level"],
        explanation=normalized["explanation"],
        examples=json.dumps(normalized["examples"]),
        exercises=json.dumps(normalized["exercises"]),
        based_on_errors=error_summary,
    )
    db.add(stored)
    await db.flush()
    await db.refresh(stored)

    result = _serialize_generated_lesson(stored)
    result["generated"] = True
    return result
