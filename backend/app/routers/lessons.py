from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Correction, Message, Session, User

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


@router.get("/library")
async def list_library(
    current_user: User = Depends(get_current_user),
):
    return _load_static_lessons()


@router.get("/library/{lesson_id}")
async def get_lesson(
    lesson_id: str,
    current_user: User = Depends(get_current_user),
):
    for lesson in _load_static_lessons():
        if lesson["id"] == lesson_id:
            return lesson
    raise HTTPException(status_code=404, detail="Lesson not found")


@router.post("/library/{lesson_id}/exercise")
async def check_exercise(
    lesson_id: str,
    exercise_index: int,
    answer: str,
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
    if exercise_index < 0 or exercise_index >= len(exercises):
        raise HTTPException(status_code=400, detail="Invalid exercise index")

    exercise = exercises[exercise_index]
    correct_answer = exercise.get("answer", "")
    is_correct = answer.strip().lower() == correct_answer.strip().lower()

    return {
        "correct": is_correct,
        "correct_answer": correct_answer,
        "explanation": exercise.get("explanation", ""),
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
        f"Include 3-5 exercises. Respond with ONLY valid JSON."
    )

    result = await llm.complete_with_fallback(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="You are an English language teacher. Create focused, practical lessons. Respond in valid JSON only.",
        task="lesson",
        temperature=0.5,
        max_tokens=1500,
    )

    from app.llm.router import parse_llm_json
    lesson_data = parse_llm_json(result["content"])
    lesson_data["generated"] = True
    lesson_data["based_on_errors"] = error_summary

    return lesson_data
