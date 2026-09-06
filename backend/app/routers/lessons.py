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
    return [
        {
            "id": l.id,
            "title": l.title,
            "topic": l.topic,
            "level": l.level,
            "explanation": l.explanation,
            "examples": json.loads(l.examples) if l.examples else [],
            # Answers stripped — grading happens via the exercise endpoint.
            "exercises": [
                {k: v for k, v in ex.items() if k != "answer"}
                for ex in (json.loads(l.exercises) if l.exercises else [])
            ],
            "based_on_errors": l.based_on_errors,
            "completed": l.completed,
            "completed_at": l.completed_at.isoformat() if l.completed_at else None,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in lessons
    ]


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
    db.expire(lesson)

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
        f"Return JSON with this structure:\n"
        f'{{"title": "...", "topic": "...", "explanation": "...", "examples": ["...", "..."], '
        f'"exercises": [{{"question": "...", "type": "fill_blank|multiple_choice", "answer": "...", "options": ["..."]}}]}}\n'
        f"Include 3-5 exercises. Respond with ONLY valid JSON — no markdown code fences, no text before or after it."
    )

    try:
        result = await llm.complete_with_fallback(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are an English language teacher. Create focused, practical lessons. Respond in valid JSON only.",
            task="lesson",
            temperature=0.5,
            max_tokens=1500,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to generate lesson: {e}")

    try:
        lesson_data = parse_lesson_json(result["content"])
    except ValueError as e:
        raise HTTPException(status_code=503, detail=f"LLM returned malformed lesson JSON: {e}")

    lesson_data["generated"] = True
    lesson_data["based_on_errors"] = error_summary

    def _as_str(value) -> str | None:
        return str(value) if value else None

    # Normalize LLM output — it can return strings/dicts where lists are
    # expected, which would break listing and the frontend later.
    def _as_list(value) -> list:
        # The LLM sometimes double-encodes lists as JSON strings.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [value] if value else []
        return value if isinstance(value, list) else [value] if value else []

    examples = _as_list(lesson_data.get("examples", []))
    examples = [str(ex) for ex in examples]
    exercises = _as_list(lesson_data.get("exercises", []))
    # Exercises may arrive as JSON-encoded strings or dicts missing keys —
    # coerce what's usable and drop the rest so we never persist junk.
    normalized_exercises: list[dict] = []
    for ex in exercises:
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
    exercises = normalized_exercises

    if not exercises:
        raise HTTPException(
            status_code=503,
            detail="LLM returned a lesson without valid exercises. Try again.",
        )

    # Persist the generated lesson so it survives navigation/reloads
    stored = GeneratedLesson(
        user_id=current_user.id,
        title=_as_str(lesson_data.get("title")) or "Personalized Lesson",
        topic=_as_str(lesson_data.get("topic")),
        level=_as_str(lesson_data.get("level")),
        explanation=str(lesson_data.get("explanation", "")),
        examples=json.dumps(examples),
        exercises=json.dumps(exercises),
        based_on_errors=error_summary,
    )
    db.add(stored)
    await db.flush()
    await db.refresh(stored)

    # Same contract as the list endpoint — answers never leave the server
    # (ADR-003/005); grading happens via the exercise check endpoint.
    # Identical shape matters: the frontend discriminates generated lessons
    # by the presence of `completed`, so a fresh lesson must carry it too.
    return {
        "id": stored.id,
        "title": stored.title,
        "topic": stored.topic,
        "level": stored.level,
        "explanation": stored.explanation,
        "examples": examples,
        "exercises": [{k: v for k, v in ex.items() if k != "answer"} for ex in exercises],
        "based_on_errors": stored.based_on_errors,
        "completed": stored.completed,
        "completed_at": None,
        "created_at": stored.created_at.isoformat() if stored.created_at else None,
        "generated": True,
    }
