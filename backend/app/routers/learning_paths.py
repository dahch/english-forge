from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.llm.prompts import (
    LEARNING_PATH_GENERATION_PROMPT,
    LEVEL_LESSONS_REQUIRED,
    NEXT_LEVEL,
    get_curriculum_seed,
    get_specialized_lesson_prompt,
)
from app.llm.router import LLMRouter, parse_llm_json, parse_lesson_json
from app.models.models import (
    Assessment,
    LearningPath,
    Message,
    PathLesson,
    Scenario,
    Session,
    Setting,
    User,
)
from app.utils import bump_daily_lessons

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning-paths", tags=["learning-paths"])


class PathLessonResponse(BaseModel):
    id: str
    path_id: str
    lesson_type: str
    topic: str
    description: str
    # Stored as a JSON string in the DB; the before-validator parses it into an
    # object, so the annotation must be dict — str would fail response
    # validation AFTER the path was persisted (500 with a saved-but-unreadable
    # path, the "Internal Server Error" seen in production).
    content: dict[str, Any] | None
    order: int
    completed: bool
    completed_at: datetime | None

    model_config = {"from_attributes": True}

    @field_validator("content", mode="before")
    @classmethod
    def parse_json_content(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except Exception:
                # Corrupt row must not 500 the whole path response.
                return None
            # Valid JSON but not an object (double-encoded list/number)
            # would fail the dict annotation — degrade like corrupt content.
            return parsed if isinstance(parsed, dict) else None
        return v


class LearningPathResponse(BaseModel):
    id: str
    current_level: str
    target_level: str
    lessons_required: int
    lessons_completed: int
    created_at: datetime
    completed_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}


class FullLearningPathResponse(LearningPathResponse):
    path_lessons: list[PathLessonResponse]


class GeneratePathRequest(BaseModel):
    assessment_id: str | None = None


class CompleteLessonRequest(BaseModel):
    completed: bool = True


class PathLessonDetailResponse(BaseModel):
    """A single path lesson with its (generated) interactive content.

    `content` is the parsed JSON column: focus/lesson_type metadata plus, once
    generated, explanation/examples/exercises. Answers are stripped before
    serialization — grading happens via the exercise endpoint.
    """

    id: str
    path_id: str
    lesson_type: str
    topic: str
    description: str
    content: dict[str, Any] | None
    order: int
    completed: bool
    completed_at: datetime | None


class PathExerciseSubmission(BaseModel):
    exercise_index: int
    answer: str


def _lesson_content_dict(lesson: PathLesson) -> dict[str, Any]:
    """Parse the content JSON column into a dict (corrupt rows degrade to {})."""
    try:
        parsed = json.loads(lesson.content or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _lesson_detail_response(lesson: PathLesson, content: dict[str, Any]) -> PathLessonDetailResponse:
    stripped = {k: v for k, v in content.items() if k != "exercises"}
    stripped["exercises"] = [
        {k: v for k, v in ex.items() if k != "answer"}
        for ex in (content.get("exercises") or [])
        if isinstance(ex, dict)
    ]
    return PathLessonDetailResponse(
        id=lesson.id,
        path_id=lesson.path_id,
        lesson_type=lesson.lesson_type,
        topic=lesson.topic,
        description=lesson.description,
        content=stripped,
        order=lesson.order,
        completed=lesson.completed,
        completed_at=lesson.completed_at,
    )


async def _deactivate_current_paths(db: AsyncSession, user_id: str) -> None:
    result = await db.execute(select(LearningPath).where(LearningPath.user_id == user_id, LearningPath.is_active == True))
    for path in result.scalars().all():
        path.is_active = False


# Serializable responses touch the `path_lessons` relationship — it must be
# eagerly loaded, otherwise Pydantic triggers a lazy load outside the
# greenlet context (MissingGreenlet) during response validation.
# populate_existing is equally required: callers that just ran core UPDATEs
# expire the identity-mapped path/lesson rows, and expired attribute access
# during response validation would lazy-load synchronously (MissingGreenlet).
async def _get_path_with_lessons(db: AsyncSession, path_id: str) -> LearningPath:
    result = await db.execute(
        select(LearningPath)
        .where(LearningPath.id == path_id)
        .options(selectinload(LearningPath.path_lessons))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _get_active_path(db: AsyncSession, user_id: str) -> LearningPath | None:
    result = await db.execute(
        select(LearningPath)
        .where(LearningPath.user_id == user_id, LearningPath.is_active == True)
        .order_by(LearningPath.created_at.desc())
        .options(selectinload(LearningPath.path_lessons))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _is_content_generated(lesson_type: str, content: dict[str, Any]) -> bool:
    if not content:
        return False
    lt = (lesson_type or "").lower()
    if lt == "conversation":
        return bool(content.get("scenario_context") and (content.get("target_expressions") or content.get("goal")))
    if lt == "vocabulary":
        return bool(content.get("items") or content.get("vocab_items"))
    if lt == "listening":
        return bool(content.get("passage"))
    if lt in ("reading", "writing"):
        return bool(content.get("passage") or content.get("writing_prompt"))
    if lt == "grammar":
        return bool(content.get("exercises") or (content.get("explanation") and content.get("formula")))
    return bool(content.get("exercises") or content.get("explanation"))


def _clean_str(s: str) -> str:
    return " ".join(s.strip().strip(".,;:!?\"'“”‘’").lower().split())


def _resolve_mcq_answer(raw_answer: Any, options: list[str]) -> str:
    if not options:
        return str(raw_answer).strip() if raw_answer is not None else ""

    if raw_answer is None:
        return ""

    # Integer index: e.g. 0, 1, 2
    if isinstance(raw_answer, int) and 0 <= raw_answer < len(options):
        return options[raw_answer]

    raw_str = str(raw_answer).strip()
    if not raw_str:
        return ""

    # Integer index as string: e.g. "0", "1"
    if raw_str.isdigit():
        idx = int(raw_str)
        if 0 <= idx < len(options):
            return options[idx]

    # Direct match against options (exact or with cleaned punctuation)
    for opt in options:
        if _clean_str(opt) == _clean_str(raw_str):
            return opt

    # Single letter: "A", "B", "C", "a", "b"...
    if len(raw_str) == 1 and raw_str.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        letter_idx = ord(raw_str.upper()) - ord("A")
        if 0 <= letter_idx < len(options):
            return options[letter_idx]

    # Letter with delimiter / prefix: "A) text", "A. text", "A: text", "A - text"
    match = re.match(r"^([A-Za-z])[\.\)\:\-\s]\s*(.*)$", raw_str)
    if match:
        letter = match.group(1).upper()
        rest = match.group(2).strip()
        letter_idx = ord(letter) - ord("A")
        if rest:
            for opt in options:
                if _clean_str(opt) == _clean_str(rest):
                    return opt
        if 0 <= letter_idx < len(options):
            return options[letter_idx]

    # "Option A", "Option B"
    opt_match = re.match(r"^Option\s+([A-Za-z])$", raw_str, re.IGNORECASE)
    if opt_match:
        letter_idx = ord(opt_match.group(1).upper()) - ord("A")
        if 0 <= letter_idx < len(options):
            return options[letter_idx]

    return raw_str


def _normalize_exercises(raw_exercises: Any) -> list[dict]:
    if isinstance(raw_exercises, str):
        try:
            raw_exercises = json.loads(raw_exercises)
        except Exception:
            return []
    if not isinstance(raw_exercises, list):
        return []

    normalized = []
    for ex in raw_exercises:
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
        ex_type = str(ex.get("type", "multiple_choice"))
        options = ex.get("options")
        if isinstance(options, str):
            try:
                options = json.loads(options)
            except Exception:
                options = None
        if not isinstance(options, list):
            options = None
        else:
            options = [str(opt) for opt in options]

        raw_ans = ex.get("answer", "")
        answer = _resolve_mcq_answer(raw_ans, options) if options else str(raw_ans).strip()
        explanation = str(ex.get("explanation", "")).strip()

        normalized.append({
            "question": question,
            "type": ex_type,
            "options": options,
            "answer": answer,
            "explanation": explanation,
        })
    return normalized


def _normalize_conversation_lesson(data: dict, topic: str, description: str, level: str) -> dict[str, Any]:
    scenario_context = str(data.get("scenario_context") or description or f"A realistic conversation scenario about {topic}.").strip()
    goal = str(data.get("goal") or f"Successfully practice communicating ideas about {topic}.").strip()
    tutor_role = str(data.get("tutor_role") or "An encouraging native English tutor.").strip()

    raw_exprs = data.get("target_expressions") or data.get("expressions") or data.get("examples") or []
    if isinstance(raw_exprs, str):
        try:
            raw_exprs = json.loads(raw_exprs)
        except Exception:
            raw_exprs = [raw_exprs]
    expressions = [str(x).strip() for x in (raw_exprs if isinstance(raw_exprs, list) else []) if str(x).strip()]
    if not expressions:
        expressions = [
            f"I would like to discuss {topic}.",
            "Could you tell me more about that?",
            "In my experience...",
            "What do you suggest in this situation?",
        ]

    explanation = str(data.get("explanation") or f"In this conversation lesson, you'll roleplay a real-world scenario focused on {topic}.").strip()

    return {
        "title": str(data.get("title") or topic),
        "explanation": explanation,
        "scenario_context": scenario_context,
        "goal": goal,
        "tutor_role": tutor_role,
        "target_expressions": expressions,
        "exercises": [],
    }


def _normalize_vocabulary_lesson(data: dict, topic: str, description: str, level: str) -> dict[str, Any]:
    explanation = str(data.get("explanation") or f"Essential vocabulary words and phrases for {topic}.").strip()
    raw_items = data.get("items") or data.get("vocab_items") or data.get("words") or []
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except Exception:
            raw_items = []

    items = []
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict) and it.get("word"):
                items.append({
                    "word": str(it.get("word", "")).strip(),
                    "definition": str(it.get("definition", "")).strip(),
                    "ipa": str(it.get("ipa", "")).strip() if it.get("ipa") else None,
                    "example": str(it.get("example", "")).strip(),
                })

    if not items:
        items = [
            {"word": topic, "definition": f"Core concept related to {topic}", "ipa": None, "example": f"We focused on {topic} today."},
            {"word": "practice", "definition": "Repeated exercise in an activity to improve proficiency", "ipa": "/ˈpræktɪs/", "example": "Daily practice builds fluency."},
            {"word": "fluency", "definition": "The ability to express oneself easily and articulately", "ipa": "/ˈfluːənsi/", "example": "Consistent conversation leads to natural fluency."},
        ]

    exercises = _normalize_exercises(data.get("exercises", []))

    return {
        "title": str(data.get("title") or topic),
        "explanation": explanation,
        "items": items,
        "vocab_items": items,
        "exercises": exercises,
    }


def _normalize_listening_lesson(data: dict, topic: str, description: str, level: str) -> dict[str, Any]:
    passage = str(data.get("passage") or data.get("text") or "").strip()
    if not passage:
        passage = f"Welcome to our listening exercise on {topic}. {description} When listening in English, pay attention to the key stress words and transitions to follow the speaker's main points."
    transcript = str(data.get("transcript") or passage).strip()
    explanation = str(data.get("explanation") or f"Listen to the passage about {topic} and answer the comprehension questions.").strip()
    exercises = _normalize_exercises(data.get("exercises", []))

    if not exercises:
        exercises = [
            {
                "question": f"What is the main topic of the listening passage?",
                "type": "multiple_choice",
                "options": [topic, "A different subject", "General introductions", "Grammar rules"],
                "answer": topic,
                "explanation": f"The passage specifically explores {topic}.",
            }
        ]

    return {
        "title": str(data.get("title") or topic),
        "explanation": explanation,
        "passage": passage,
        "transcript": transcript,
        "exercises": exercises,
    }


def _normalize_grammar_lesson(data: dict, topic: str, description: str, level: str) -> dict[str, Any]:
    explanation = str(data.get("explanation") or f"Learn and practice the grammar principles for {topic}.").strip()
    formula = str(data.get("formula") or "").strip()
    raw_examples = data.get("examples") or []
    if isinstance(raw_examples, str):
        try:
            raw_examples = json.loads(raw_examples)
        except Exception:
            raw_examples = [raw_examples]
    examples = [str(x).strip() for x in (raw_examples if isinstance(raw_examples, list) else []) if str(x).strip()]
    if not examples:
        examples = [f"Example showing correct usage for {topic}."]

    exercises = _normalize_exercises(data.get("exercises", []))
    if not exercises:
        exercises = [
            {
                "question": f"Choose the correct usage for {topic}:",
                "type": "multiple_choice",
                "options": ["Correct option", "Incorrect form A", "Incorrect form B"],
                "answer": "Correct option",
                "explanation": f"Demonstrates proper rule adherence for {topic}.",
            }
        ]

    return {
        "title": str(data.get("title") or topic),
        "explanation": explanation,
        "formula": formula,
        "examples": examples,
        "exercises": exercises,
    }


def _normalize_reading_writing_lesson(data: dict, topic: str, description: str, level: str) -> dict[str, Any]:
    passage = str(data.get("passage") or data.get("text") or "").strip()
    if not passage:
        passage = f"{topic} represents an essential dimension of English communication. {description} Developing strong reading comprehension enables deeper expression in writing."
    explanation = str(data.get("explanation") or f"Read the passage carefully and respond to the writing prompt.").strip()
    writing_prompt = str(data.get("writing_prompt") or f"In 50-80 words, write your perspective on {topic} applying what you learned.").strip()

    raw_examples = data.get("examples") or []
    if isinstance(raw_examples, str):
        try:
            raw_examples = json.loads(raw_examples)
        except Exception:
            raw_examples = [raw_examples]
    examples = [str(x).strip() for x in (raw_examples if isinstance(raw_examples, list) else []) if str(x).strip()]

    exercises = _normalize_exercises(data.get("exercises", []))
    if not exercises:
        exercises = [
            {
                "question": f"What key point does the passage convey regarding {topic}?",
                "type": "multiple_choice",
                "options": [f"Key insight into {topic}", "An unrelated observation", "A contradictory claim"],
                "answer": f"Key insight into {topic}",
                "explanation": f"Supported directly by the text regarding {topic}.",
            }
        ]

    return {
        "title": str(data.get("title") or topic),
        "explanation": explanation,
        "passage": passage,
        "writing_prompt": writing_prompt,
        "examples": examples,
        "exercises": exercises,
    }


def normalize_specialized_lesson(
    lesson_type: str,
    data: dict,
    topic: str,
    description: str,
    level: str,
) -> dict[str, Any]:
    lt = (lesson_type or "grammar").lower()
    if lt == "conversation":
        return _normalize_conversation_lesson(data, topic, description, level)
    elif lt == "vocabulary":
        return _normalize_vocabulary_lesson(data, topic, description, level)
    elif lt == "listening":
        return _normalize_listening_lesson(data, topic, description, level)
    elif lt in ("reading", "writing"):
        return _normalize_reading_writing_lesson(data, topic, description, level)
    else:
        return _normalize_grammar_lesson(data, topic, description, level)


async def _generate_path_for_user(
    db: AsyncSession,
    current_user: User,
    assessment: Assessment | None = None,
) -> LearningPath:
    current_level = (current_user.current_level or "").strip().upper()
    if not current_level:
        cefr_setting = await db.execute(
            select(Setting).where(Setting.user_id == current_user.id, Setting.key == "default_cefr")
        )
        preferred = cefr_setting.scalar_one_or_none()
        current_level = (preferred.value or "").strip().upper() if preferred else ""
    if not current_level:
        current_level = "A1"
    target_level = NEXT_LEVEL.get(current_level)
    if not target_level:
        raise HTTPException(status_code=400, detail="You have already reached C2 — the highest level. Keep practicing!")

    lessons_required = LEVEL_LESSONS_REQUIRED.get(current_level, 20)

    assessment_info = ""
    if assessment:
        strengths = json.loads(assessment.strengths) if assessment.strengths else []
        weaknesses = json.loads(assessment.weaknesses) if assessment.weaknesses else []
        recommendations = json.loads(assessment.recommendations) if assessment.recommendations else []
        assessment_info = (
            f"Estimated level: {assessment.estimated_level}\n"
            f"Strengths: {', '.join(strengths)}\n"
            f"Weaknesses: {', '.join(weaknesses)}\n"
            f"Recommendations: {', '.join(recommendations)}\n"
            f"Summary: {assessment.summary or 'N/A'}\n"
        )

    prompt = (
        f"{LEARNING_PATH_GENERATION_PROMPT}\n\n"
        f"Learner current level: {current_level}\n"
        f"Target level: {target_level}\n"
        f"Number of lessons required: {lessons_required}\n\n"
        f"{assessment_info}\n"
        f"Generate exactly {lessons_required} lessons in the JSON array."
    )

    llm = LLMRouter(db, current_user.id)
    lessons_data: list[dict] = []
    parsed: dict = {}
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                llm.complete_with_fallback(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="You are an expert English curriculum designer. Return valid JSON only.",
                    task="lesson",
                    temperature=0.5,
                    max_tokens=4000,
                ),
                timeout=25.0,
            )
        except Exception as e:
            logger.warning(f"Learning path LLM call attempt {attempt + 1}/2 failed or timed out for user {current_user.id}: {e}")
            continue

        raw_content = result.get("content", "")
        provider_used = result.get("provider", "unknown")
        model_used = result.get("model", "unknown")
        logger.info(
            f"Learning path raw LLM response for user {current_user.id} "
            f"(provider={provider_used}, model={model_used}, attempt={attempt + 1}/2, length={len(raw_content)}): "
            f"{raw_content[:2000]}"
        )

        if not raw_content.strip():
            logger.warning(f"Learning path LLM returned empty content (attempt {attempt + 1}/2)")
            continue

        try:
            parsed = parse_llm_json(raw_content)
        except Exception as e:
            logger.warning(f"Learning path JSON parsing failed for user {current_user.id} (attempt {attempt + 1}/2): {e}")
            continue

        # Tolerate a bare lessons array
        if isinstance(parsed, list):
            parsed = {"lessons": parsed}
        if not isinstance(parsed, dict):
            logger.warning(
                f"Learning path parsed into unusable shape for user {current_user.id} "
                f"(attempt {attempt + 1}/2): {type(parsed).__name__}"
            )
            continue

        lessons_data = parsed.get("lessons") or parsed.get("items") or []
        if isinstance(lessons_data, str):
            try:
                lessons_data = json.loads(lessons_data)
            except Exception:
                lessons_data = []
        if isinstance(lessons_data, dict):
            lessons_data = [lessons_data]
        lessons_data = [lesson for lesson in (lessons_data or []) if isinstance(lesson, dict)]
        if lessons_data:
            break

        logger.warning(
            f"Learning path response had no usable lessons (attempt {attempt + 1}/2): "
            f"keys={list(parsed.keys())}, raw_tail=...{raw_content[-300:]}"
        )

    # Graceful fallback to curriculum template seed:
    if not lessons_data:
        logger.warning(
            f"Generated learning path had no usable lessons or timed out for user {current_user.id}. "
            f"Falling back gracefully to curriculum template seed for {current_level}->{target_level}."
        )
        lessons_data = get_curriculum_seed(current_level, target_level, assessment=assessment)

    logger.info(
        f"Learning path ready for user {current_user.id}: {len(lessons_data)} lessons."
    )

    lessons_data = lessons_data[:lessons_required]
    lessons_required = len(lessons_data)

    path = LearningPath(
        user_id=current_user.id,
        assessment_id=assessment.id if assessment else None,
        current_level=current_level,
        target_level=target_level,
        lessons_required=lessons_required,
        lessons_completed=0,
        is_active=True,
    )
    try:
        async with db.begin_nested():
            await _deactivate_current_paths(db, current_user.id)
            db.add(path)
    except IntegrityError:
        existing = await _get_active_path(db, current_user.id)
        if existing:
            return existing
        raise

    for i, lesson_data in enumerate(lessons_data):
        path_lesson = PathLesson(
            path_id=path.id,
            lesson_type=lesson_data.get("lesson_type", "grammar"),
            topic=lesson_data.get("topic", "Untitled")[:200],
            description=lesson_data.get("description", ""),
            content=json.dumps({
                "focus": lesson_data.get("topic", ""),
                "lesson_type": lesson_data.get("lesson_type", "grammar"),
            }),
            order=i + 1,
        )
        db.add(path_lesson)

    await db.flush()

    logger.info(
        f"Generated learning path {path.id} for user {current_user.id}: "
        f"{path.current_level}->{path.target_level}, {len(lessons_data)} lessons"
    )

    return await _get_path_with_lessons(db, path.id)


@router.get("/current", response_model=FullLearningPathResponse)
async def get_current_path(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    path = await _get_active_path(db, current_user.id)
    if not path:
        raise HTTPException(status_code=404, detail="No active learning path. Complete the assessment or generate one manually.")

    return path


@router.post("/generate", response_model=FullLearningPathResponse, status_code=201)
async def generate_path(
    body: GeneratePathRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    assessment: Assessment | None = None
    if body.assessment_id:
        result = await db.execute(
            select(Assessment).where(Assessment.id == body.assessment_id, Assessment.user_id == current_user.id)
        )
        assessment = result.scalar_one_or_none()
        if not assessment:
            # Don't silently generate a generic path when the client thinks
            # it's assessment-informed — say so.
            raise HTTPException(status_code=404, detail="Assessment not found")

    path = await _generate_path_for_user(db, current_user, assessment)
    return path


@router.patch("/{path_id}/lessons/{lesson_id}/complete", response_model=FullLearningPathResponse)
async def complete_lesson(
    path_id: str,
    lesson_id: str,
    body: CompleteLessonRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    path_result = await db.execute(
        select(LearningPath).where(LearningPath.id == path_id, LearningPath.user_id == current_user.id)
    )
    path = path_result.scalar_one_or_none()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    lesson_result = await db.execute(
        select(PathLesson).where(PathLesson.id == lesson_id, PathLesson.path_id == path_id)
    )
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    completed = body.completed if body is not None else True

    # Atomic state transition — the WHERE clause rejects rows already in the
    # requested state, so concurrent/double-clicked requests can't double-count.
    transition = await db.execute(
        update(PathLesson)
        .where(PathLesson.id == lesson_id, PathLesson.completed == (not completed))
        .values(
            completed=completed,
            completed_at=datetime.now(timezone.utc) if completed else None,
        )
    )

    if transition.rowcount:
        delta = 1 if completed else -1
        await db.execute(
            update(LearningPath)
            .where(LearningPath.id == path_id)
            .values(lessons_completed=LearningPath.lessons_completed + delta)
        )
        await bump_daily_lessons(db, current_user.id, delta)

    await db.flush()

    # Core UPDATEs bypass the ORM identity map, but the reload below runs with
    # populate_existing, refreshing the identity-mapped rows in async context.
    # Never db.expire() here: expired attribute access (even path.id) lazy-loads
    # synchronously outside the greenlet → MissingGreenlet (production 500).

    return await _get_path_with_lessons(db, path.id)


async def _generate_lesson_content(
    db: AsyncSession,
    user: User,
    path: LearningPath,
    lesson: PathLesson,
) -> dict[str, Any]:
    assessment_info = ""
    if path.assessment_id:
        a_result = await db.execute(
            select(Assessment).where(
                Assessment.id == path.assessment_id, Assessment.user_id == user.id
            )
        )
        assessment = a_result.scalar_one_or_none()
        if assessment:
            strengths = json.loads(assessment.strengths) if assessment.strengths else []
            weaknesses = json.loads(assessment.weaknesses) if assessment.weaknesses else []
            assessment_info = (
                f"\nLearner assessment context (personalize toward weaknesses):\n"
                f"Estimated level: {assessment.estimated_level or path.current_level}\n"
                f"Weaknesses: {', '.join(weaknesses) or 'N/A'}\n"
                f"Strengths: {', '.join(strengths) or 'N/A'}\n"
                f"Summary: {assessment.summary or 'N/A'}\n"
            )

    prompt = get_specialized_lesson_prompt(
        lesson_type=lesson.lesson_type,
        topic=lesson.topic,
        description=lesson.description,
        current_level=path.current_level,
        target_level=path.target_level,
        assessment_info=assessment_info,
    )

    lesson_data: dict | None = None
    llm = LLMRouter(db, user.id)

    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                llm.complete_with_fallback(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt=(
                        "You are an expert English teacher. Return valid JSON only without markdown code blocks."
                    ),
                    task="lesson",
                    temperature=0.5,
                    max_tokens=2000,
                ),
                timeout=20.0,
            )
            raw_content = result.get("content", "")
            lesson_data = parse_lesson_json(raw_content)
            if lesson_data and any(
                k in lesson_data
                for k in ("scenario_context", "items", "vocab_items", "passage", "exercises", "explanation", "goal")
            ):
                break
        except Exception as e:
            logger.warning(
                f"Path lesson generation attempt {attempt + 1}/2 failed or timed out (lesson {lesson.id}): {e}"
            )
            lesson_data = None

    if not lesson_data:
        raise HTTPException(
            status_code=503,
            detail="Failed to generate the lesson content. Please try again.",
        )

    normalized = normalize_specialized_lesson(
        lesson.lesson_type,
        lesson_data,
        lesson.topic,
        lesson.description,
        path.current_level,
    )
    return normalized


@router.post("/{path_id}/lessons/{lesson_id}/generate", response_model=PathLessonDetailResponse)
async def generate_path_lesson(
    path_id: str,
    lesson_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate (lazily, idempotently) the interactive content for a path lesson.

    Tailored to the lesson's type/topic, the path's level span, and the learner's
    assessment profile. Supports conversation, vocabulary, listening, grammar,
    and reading/writing with clean normalizations and answers stripped before sending.
    """
    path_result = await db.execute(
        select(LearningPath).where(LearningPath.id == path_id, LearningPath.user_id == current_user.id)
    )
    path = path_result.scalar_one_or_none()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    lesson_result = await db.execute(
        select(PathLesson).where(PathLesson.id == lesson_id, PathLesson.path_id == path_id)
    )
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    content = _lesson_content_dict(lesson)
    if _is_content_generated(lesson.lesson_type, content):
        return _lesson_detail_response(lesson, content)

    generated_content = await _generate_lesson_content(db, current_user, path, lesson)
    content.update(generated_content)
    lesson.content = json.dumps(content, ensure_ascii=False)
    await db.flush()

    logger.info(f"Generated content for path lesson {lesson.id} ({lesson.lesson_type}: {lesson.topic})")
    return _lesson_detail_response(lesson, content)


@router.post("/{path_id}/lessons/{lesson_id}/start-conversation")
async def start_lesson_conversation(
    path_id: str,
    lesson_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an active conversation session configured specifically for this path lesson."""
    path_result = await db.execute(
        select(LearningPath).where(LearningPath.id == path_id, LearningPath.user_id == current_user.id)
    )
    path = path_result.scalar_one_or_none()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    lesson_result = await db.execute(
        select(PathLesson).where(PathLesson.id == lesson_id, PathLesson.path_id == path_id)
    )
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    content = _lesson_content_dict(lesson)
    if not _is_content_generated(lesson.lesson_type, content):
        generated_content = await _generate_lesson_content(db, current_user, path, lesson)
        content.update(generated_content)
        lesson.content = json.dumps(content, ensure_ascii=False)
        await db.flush()

    scenario_context = content.get("scenario_context") or lesson.description
    goal = content.get("goal") or f"Practice speaking about {lesson.topic}"
    tutor_role = content.get("tutor_role") or "A friendly and patient English conversation partner"
    target_expressions = content.get("target_expressions") or []

    expressions_str = (
        "\n".join(f"- {exp}" for exp in target_expressions)
        if target_expressions
        else f"- Natural expressions related to {lesson.topic}"
    )
    system_prompt = (
        f"You are engaging in a targeted conversation practice session with an English learner at {path.current_level} level.\n\n"
        f"Topic: {lesson.topic}\n"
        f"Scenario Context: {scenario_context}\n"
        f"Learning Goal: {goal}\n"
        f"Your Role: {tutor_role}\n\n"
        f"Target Expressions to elicit or practice:\n{expressions_str}\n\n"
        f"Stay in character. Prompt the student naturally, encourage their responses, and gently help them use the target expressions. "
        f"Keep each reply concise (1-3 sentences) to maintain conversation momentum."
    )

    scenario_name = f"Lesson: {lesson.topic}"[:200]
    existing_scenario = await db.execute(
        select(Scenario).where(
            Scenario.user_id == current_user.id,
            Scenario.name == scenario_name,
            Scenario.is_custom.is_(True),
        )
    )
    scenario = existing_scenario.scalars().first()
    if scenario:
        scenario.system_prompt = system_prompt
        scenario.cefr_level = path.current_level
    else:
        scenario = Scenario(
            user_id=current_user.id,
            name=scenario_name,
            system_prompt=system_prompt,
            cefr_level=path.current_level,
            is_custom=True,
        )
        db.add(scenario)
        await db.flush()

    session = Session(
        user_id=current_user.id,
        scenario_id=scenario.id,
        cefr_level=path.current_level,
    )
    db.add(session)
    await db.flush()

    # Create initial greeting in character from the tutor
    greeting_text = (
        f"Hello! Welcome to our session on '{lesson.topic}'. "
        f"{scenario_context} "
        f"Our goal is: {goal}. When you're ready, let me know how you'd like to begin!"
    )
    greeting_msg = Message(
        session_id=session.id,
        role="assistant",
        text=greeting_text,
    )
    db.add(greeting_msg)
    await db.flush()

    return {"session_id": session.id}


@router.post("/{path_id}/lessons/{lesson_id}/exercise")
async def check_path_lesson_exercise(
    path_id: str,
    lesson_id: str,
    body: PathExerciseSubmission,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grade one exercise of a path lesson. Answers never leave the server —
    they live only in the stored content JSON."""
    path_result = await db.execute(
        select(LearningPath).where(LearningPath.id == path_id, LearningPath.user_id == current_user.id)
    )
    if not path_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Learning path not found")

    lesson_result = await db.execute(
        select(PathLesson).where(PathLesson.id == lesson_id, PathLesson.path_id == path_id)
    )
    lesson = lesson_result.scalar_one_or_none()
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")

    exercises = _lesson_content_dict(lesson).get("exercises") or []
    if not isinstance(exercises, list) or not 0 <= body.exercise_index < len(exercises):
        raise HTTPException(status_code=400, detail="Invalid exercise index")
    exercise = exercises[body.exercise_index]
    if not isinstance(exercise, dict):
        raise HTTPException(status_code=400, detail="Invalid exercise index")

    correct_answer = str(exercise.get("answer", ""))
    user_ans = body.answer.strip()
    options = exercise.get("options")
    if isinstance(options, list) and options:
        user_ans = _resolve_mcq_answer(user_ans, [str(o) for o in options])

    is_correct = _clean_str(user_ans) == _clean_str(correct_answer)
    return {
        "correct": is_correct,
        "correct_answer": correct_answer,
        "explanation": str(exercise.get("explanation", "")),
    }


@router.post("/{path_id}/advance", response_model=FullLearningPathResponse)
async def advance_level(
    path_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    path_result = await db.execute(
        select(LearningPath).where(LearningPath.id == path_id, LearningPath.user_id == current_user.id)
    )
    path = path_result.scalar_one_or_none()
    if not path:
        raise HTTPException(status_code=404, detail="Learning path not found")

    if path.lessons_completed < path.lessons_required:
        raise HTTPException(
            status_code=400,
            detail=f"Complete {path.lessons_required - path.lessons_completed} more lessons before advancing"
        )

    # Atomic claim — the WHERE clause ensures only one of several concurrent
    # (double-clicked) requests advances the path and generates the next one.
    claim = await db.execute(
        update(LearningPath)
        .where(LearningPath.id == path_id, LearningPath.completed_at.is_(None))
        .values(completed_at=datetime.now(timezone.utc), is_active=False)
    )
    if not claim.rowcount:
        raise HTTPException(status_code=409, detail="This path has already been advanced")

    # Advance user level
    current_user.current_level = path.target_level

    # Keep default_cefr setting in sync
    setting_res = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id, Setting.key == "default_cefr")
    )
    setting = setting_res.scalar_one_or_none()
    if setting:
        setting.value = path.target_level
    else:
        db.add(Setting(user_id=current_user.id, key="default_cefr", value=path.target_level))

    await db.flush()
    # No db.expire(path) here — expired attribute access lazy-loads
    # synchronously outside the greenlet (MissingGreenlet). The stale object
    # is never read again; _generate_path_for_user returns a fresh path.

    # Generate the next path automatically — same shared service as
    # POST /generate, without assessment context (the old path is done).
    return await _generate_path_for_user(db, current_user)
