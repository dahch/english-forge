from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.json_utils import as_dict_list, load_json
from app.llm.router import parse_lesson_json
from app.models.models import Correction, GeneratedLesson, Message, Session, User, LibraryLessonCompletion
from app.utils import bump_daily_lessons

router = APIRouter(prefix="/api/lessons", tags=["lessons"])

LESSONS_DIR = Path(__file__).parent.parent / "lessons" / "data"
_CACHED_STATIC_LESSONS: list[dict] | None = None


def _load_static_lessons() -> list[dict]:
    global _CACHED_STATIC_LESSONS
    if _CACHED_STATIC_LESSONS is not None:
        return _CACHED_STATIC_LESSONS
    lessons = []
    if LESSONS_DIR.exists():
        for f in sorted(LESSONS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["id"] = f.stem
                lessons.append(data)
            except Exception:
                continue
    _CACHED_STATIC_LESSONS = lessons
    return lessons


def _strip_answers(lesson: dict) -> dict:
    """Answers and explanations never leave the server upfront — grading and explanation happen via check endpoint."""
    sanitized = {k: v for k, v in lesson.items() if k != "exercises"}
    sanitized["exercises"] = [
        {k: v for k, v in ex.items() if k not in ("answer", "explanation")}
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
    examples = load_json(
        lesson.examples,
        default=[],
        expected_type=list,
        context=f"generated_lesson {lesson.id} examples",
    )
    exercises = as_dict_list(
        lesson.exercises, context=f"generated_lesson {lesson.id} exercises"
    )
    return {
        "id": lesson.id,
        "title": lesson.title,
        "topic": lesson.topic,
        "level": lesson.level,
        "explanation": lesson.explanation,
        "examples": examples,
        # Answers and explanations stripped — delivered upon exercise submission
        "exercises": [
            {k: v for k, v in ex.items() if k not in ("answer", "explanation")}
            for ex in exercises
        ],
        "based_on_errors": lesson.based_on_errors,
        "completed": lesson.completed,
        "completed_at": lesson.completed_at.isoformat() if lesson.completed_at else None,
        "created_at": lesson.created_at.isoformat() if lesson.created_at else None,
    }


class LibraryLessonCompleteRequest(BaseModel):
    completed: bool = True


@router.get("/library")
async def list_library(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    comp_result = await db.execute(
        select(LibraryLessonCompletion.lesson_id).where(
            LibraryLessonCompletion.user_id == current_user.id
        )
    )
    completed_ids = set(comp_result.scalars().all())

    return [
        _strip_answers(lesson) | {
            "exercise_count": len(lesson.get("exercises", [])),
            "completed": lesson["id"] in completed_ids,
        }
        for lesson in _load_static_lessons()
    ]


@router.get("/library/completed", response_model=list[str])
async def list_completed_library_lessons(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LibraryLessonCompletion.lesson_id).where(
            LibraryLessonCompletion.user_id == current_user.id
        )
    )
    return result.scalars().all()


@router.get("/library/{lesson_id}")
async def get_lesson(
    lesson_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    for lesson in _load_static_lessons():
        if lesson["id"] == lesson_id:
            comp_result = await db.execute(
                select(LibraryLessonCompletion).where(
                    LibraryLessonCompletion.user_id == current_user.id,
                    LibraryLessonCompletion.lesson_id == lesson_id,
                )
            )
            is_completed = comp_result.scalar_one_or_none() is not None
            return _strip_answers(lesson) | {"completed": is_completed}
    raise HTTPException(status_code=404, detail="Lesson not found")


@router.patch("/library/{lesson_id}")
async def complete_library_lesson(
    lesson_id: str,
    body: LibraryLessonCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    static_ids = [l["id"] for l in _load_static_lessons()]
    if lesson_id not in static_ids:
        raise HTTPException(status_code=404, detail="Library lesson not found")

    existing = await db.execute(
        select(LibraryLessonCompletion).where(
            LibraryLessonCompletion.user_id == current_user.id,
            LibraryLessonCompletion.lesson_id == lesson_id,
        )
    )
    record = existing.scalar_one_or_none()

    if body.completed:
        if not record:
            try:
                async with db.begin_nested():
                    comp = LibraryLessonCompletion(user_id=current_user.id, lesson_id=lesson_id)
                    db.add(comp)
                    await db.flush()
                await bump_daily_lessons(db, current_user.id, +1)
            except IntegrityError:
                pass  # Idempotent concurrent submission
    else:
        if record:
            await db.delete(record)
            await db.flush()
            await bump_daily_lessons(db, current_user.id, -1)

    await db.flush()
    return {"id": lesson_id, "lesson_id": lesson_id, "completed": body.completed}


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
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    result = await db.execute(
        select(GeneratedLesson)
        .where(GeneratedLesson.user_id == current_user.id)
        .order_by(GeneratedLesson.created_at.desc())
        .offset(offset)
        .limit(limit)
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
            GeneratedLesson.user_id == current_user.id,
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


class LessonGenerateRequest(BaseModel):
    topic: str | None = Field(default=None, max_length=100)
    level: str | None = Field(default=None, max_length=2)


FALLBACK_LESSONS_BY_LEVEL: dict[str, dict] = {
    "A1": {
        "title": "Present Simple & Daily Routines",
        "topic": "grammar",
        "level": "A1",
        "explanation": "Use the present simple tense for daily routines, habits, and general truths. Add '-s' or '-es' for third-person singular (he, she, it).",
        "examples": [
            "I drink coffee every morning.",
            "She works at a technology company.",
            "They take the bus to school on weekdays.",
        ],
        "exercises": [
            {"question": "Fill in the blank: He _______ (live) in London.", "type": "fill_blank", "answer": "lives", "options": ["live", "lives", "living"]},
            {"question": "Fill in the blank: We _______ (not / like) cold weather.", "type": "fill_blank", "answer": "do not like", "options": ["do not like", "does not like", "not like"]},
            {"question": "Which sentence is grammatically correct?", "type": "multiple_choice", "answer": "She speaks three languages fluently.", "options": ["She speak three languages fluently.", "She speaks three languages fluently.", "She speaking three languages fluently."]},
        ],
    },
    "A2": {
        "title": "Past Simple: Regular & Irregular Verbs",
        "topic": "grammar",
        "level": "A2",
        "explanation": "Use the past simple to describe completed actions in the past. Regular verbs end in '-ed', while irregular verbs have unique past forms.",
        "examples": [
            "We visited our grandparents last weekend.",
            "She bought a new laptop yesterday.",
            "Did you see the latest movie on Friday?",
        ],
        "exercises": [
            {"question": "Fill in the blank: Yesterday I _______ (go) to the supermarket.", "type": "fill_blank", "answer": "went", "options": ["go", "went", "gone"]},
            {"question": "Fill in the blank: They _______ (watch) a documentary last night.", "type": "fill_blank", "answer": "watched", "options": ["watch", "watched", "watching"]},
            {"question": "Choose the correct negative past simple sentence:", "type": "multiple_choice", "answer": "I didn't hear the alarm this morning.", "options": ["I didn't heard the alarm this morning.", "I didn't hear the alarm this morning.", "I not heard the alarm this morning."]},
        ],
    },
    "B1": {
        "title": "Present Perfect vs Past Simple",
        "topic": "grammar",
        "level": "B1",
        "explanation": "Use past simple for actions finished at a specific past time. Use present perfect for life experiences or actions with a connection to the present.",
        "examples": [
            "I have visited Japan twice in my life.",
            "She moved to Berlin in 2021.",
            "Have you ever tried rock climbing?",
        ],
        "exercises": [
            {"question": "Fill in the blank: I _______ (know) him since we were at university.", "type": "fill_blank", "answer": "have known", "options": ["knew", "have known", "am knowing"]},
            {"question": "Fill in the blank: Last year, our team _______ (win) the annual tournament.", "type": "fill_blank", "answer": "won", "options": ["won", "has won", "had won"]},
            {"question": "Which sentence correctly connects past experience to the present?", "type": "multiple_choice", "answer": "I have already finished writing the report.", "options": ["I have already finished writing the report.", "I already finished write the report yesterday.", "I have finish writing the report yet."]},
        ],
    },
    "B2": {
        "title": "Conditional Structures & Nuanced Hypotheses",
        "topic": "grammar",
        "level": "B2",
        "explanation": "Second conditionals describe unreal or unlikely present situations (if + past, would + base verb). Third conditionals discuss past regrets or counterfactuals (if + past perfect, would have + past participle).",
        "examples": [
            "If I had more free time, I would take up oil painting.",
            "If we had reviewed the contract earlier, we would have avoided the penalty.",
            "Were you to accept the offer, what would your start date be?",
        ],
        "exercises": [
            {"question": "Fill in the blank: If she _______ (study) harder, she would have passed the exam.", "type": "fill_blank", "answer": "had studied", "options": ["studied", "had studied", "would study"]},
            {"question": "Fill in the blank: If I were you, I _______ (negotiate) the salary before signing.", "type": "fill_blank", "answer": "would negotiate", "options": ["will negotiate", "would negotiate", "negotiated"]},
            {"question": "Select the correct inverted formal conditional:", "type": "multiple_choice", "answer": "Had we known about the delay, we would have informed you.", "options": ["If had we known about the delay, we would inform you.", "Had we known about the delay, we would have informed you.", "Did we know about the delay, we would have informed you."]},
        ],
    },
    "C1": {
        "title": "Advanced Inversion & Emphatic Discourse Markers",
        "topic": "grammar",
        "level": "C1",
        "explanation": "Inversion places the auxiliary verb before the subject after negative or restrictive adverbials (e.g., rarely, seldom, no sooner) to create rhetorical emphasis.",
        "examples": [
            "Seldom have I witnessed such dedication to craft.",
            "Not only did they meet the deadline, but they also exceeded expectations.",
            "Under no circumstances should confidential files be shared.",
        ],
        "exercises": [
            {"question": "Fill in the blank: Rarely _______ (we / encounter) such remarkable insight during peer review.", "type": "fill_blank", "answer": "do we encounter", "options": ["we encounter", "do we encounter", "we do encounter"]},
            {"question": "Fill in the blank: No sooner had he arrived _______ the meeting commenced.", "type": "fill_blank", "answer": "than", "options": ["when", "than", "that"]},
            {"question": "Choose the correctly inverted sentence:", "type": "multiple_choice", "answer": "Only after thorough deliberation did the committee reach consensus.", "options": ["Only after thorough deliberation the committee did reach consensus.", "Only after thorough deliberation did the committee reach consensus.", "Only after thorough deliberation reached the committee consensus."]},
        ],
    },
    "C2": {
        "title": "Subjunctive Mood & Stylistic Subtleties in Formal Prose",
        "topic": "grammar",
        "level": "C2",
        "explanation": "The mandative subjunctive employs the base form of the verb in 'that'-clauses following verbs or adjectives of urgency, recommendation, or requirement.",
        "examples": [
            "The board insisted that the chief executive resign immediately.",
            "It is imperative that every protocol be observed without deviation.",
            "Be that as it may, our strategic priorities remain unchanged.",
        ],
        "exercises": [
            {"question": "Fill in the blank: The magistrate recommended that the testimony _______ (be) struck from the record.", "type": "fill_blank", "answer": "be", "options": ["is", "was", "be"]},
            {"question": "Fill in the blank: It is vital that confidentiality _______ (maintain) at all times.", "type": "fill_blank", "answer": "be maintained", "options": ["is maintained", "be maintained", "will be maintained"]},
            {"question": "Select the sentence with accurate subjunctive formula:", "type": "multiple_choice", "answer": "We requested that he submit the revised manuscript by dawn.", "options": ["We requested that he submits the revised manuscript by dawn.", "We requested that he submit the revised manuscript by dawn.", "We requested that he will submit the revised manuscript by dawn."]},
        ],
    },
}


@router.post("/generate")
async def generate_lesson(
    body: LessonGenerateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target_level = (body.level if (body and body.level) else current_user.current_level or "B1").strip().upper()
    if target_level not in FALLBACK_LESSONS_BY_LEVEL:
        target_level = "B1"

    custom_topic = (body.topic or "").strip() if body else ""

    if custom_topic:
        error_summary = f"Custom requested topic: {custom_topic}"
        prompt = (
            f"Create a short English lesson (JSON) focused on the topic: '{custom_topic}' for a student at {target_level} level.\n"
            f"Return JSON with this EXACT structure (do not omit any field):\n"
            f'{{"title": "{custom_topic.title()}", "topic": "grammar|vocabulary|naturalness", "level": "{target_level}", "explanation": "A clear 2-3 sentence explanation of the concept", "examples": ["Example 1 showing the correct usage", "Example 2 showing the correct usage", "Example 3 showing the correct usage"], '
            f'"exercises": [{{"question": "Fill in the blank: ...", "type": "fill_blank", "answer": "correct answer", "options": ["option1", "option2", "option3"]}}]}}\n'
            f"CRITICAL: You MUST include 3-5 exercises with questions, answers, and options. Do NOT return empty arrays. Respond with ONLY valid JSON."
        )
    else:
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

        if top_errors:
            error_summary = ", ".join(f"{err_type} ({count} times)" for err_type, count in top_errors)
            prompt = (
                f"Create a short English lesson (JSON) focused on the student's most frequent errors: {error_summary} for a student at {target_level} level.\n"
                f"Return JSON with this EXACT structure (do not omit any field):\n"
                f'{{"title": "Lesson title here", "topic": "grammar|vocabulary|naturalness", "level": "{target_level}", "explanation": "A clear 2-3 sentence explanation of the concept", "examples": ["Example 1 showing the correct usage", "Example 2 showing the correct usage", "Example 3 showing the correct usage"], '
                f'"exercises": [{{"question": "Fill in the blank: ...", "type": "fill_blank", "answer": "correct answer", "options": ["option1", "option2", "option3"]}}]}}\n'
                f"CRITICAL: You MUST include 3-5 exercises with questions, answers, and options. Do NOT return empty arrays. Respond with ONLY valid JSON."
            )
        else:
            error_summary = f"Core fundamentals for {target_level} English"
            prompt = (
                f"Create a short English lesson (JSON) covering essential conversational communication skills, useful phrasal verbs, or key grammar for a student at {target_level} level.\n"
                f"Return JSON with this EXACT structure (do not omit any field):\n"
                f'{{"title": "Lesson title here", "topic": "grammar|vocabulary|naturalness", "level": "{target_level}", "explanation": "A clear 2-3 sentence explanation of the concept", "examples": ["Example 1 showing the correct usage", "Example 2 showing the correct usage", "Example 3 showing the correct usage"], '
                f'"exercises": [{{"question": "Fill in the blank: ...", "type": "fill_blank", "answer": "correct answer", "options": ["option1", "option2", "option3"]}}]}}\n'
                f"CRITICAL: You MUST include 3-5 exercises with questions, answers, and options. Do NOT return empty arrays. Respond with ONLY valid JSON."
            )

    from app.llm.router import LLMRouter
    llm = LLMRouter(db, current_user.id)

    lesson_data = None
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                llm.complete_with_fallback(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="You are an English language teacher. Create focused, practical lessons with exercises. Respond in valid JSON only. Never return empty arrays for exercises.",
                    task="lesson",
                    temperature=0.5,
                    max_tokens=1500,
                ),
                timeout=20.0,
            )
            lesson_data = parse_lesson_json(result["content"])
            if lesson_data.get("explanation") or lesson_data.get("exercises"):
                break
            logger.warning(f"Lesson generation attempt {attempt + 1}/2 returned empty content")
            lesson_data = None
        except Exception as e:
            logger.warning(f"Lesson generation attempt {attempt + 1}/2 failed: {e}")
            lesson_data = None

    normalized = normalize_llm_lesson(lesson_data) if lesson_data else None

    # Fallback to robust deterministic template if LLM failed, timed out, or returned no exercises
    used_fallback = False
    if not normalized or not normalized.get("exercises"):
        used_fallback = True
        fallback = FALLBACK_LESSONS_BY_LEVEL.get(target_level, FALLBACK_LESSONS_BY_LEVEL["B1"])
        normalized = {
            "title": f"{custom_topic.title()} ({target_level})" if custom_topic else fallback["title"],
            "topic": custom_topic if custom_topic else fallback["topic"],
            "level": target_level,
            "explanation": fallback["explanation"],
            "examples": fallback["examples"],
            "exercises": fallback["exercises"],
        }

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
    # Honesty signal for the client: "fallback" means the model failed or timed
    # out and this is the deterministic template, not personalized content.
    result["source"] = "fallback" if used_fallback else "llm"
    return result
