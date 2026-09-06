from __future__ import annotations

import json
import logging
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
from app.llm.prompts import LEARNING_PATH_GENERATION_PROMPT, LEVEL_LESSONS_REQUIRED, NEXT_LEVEL
from app.llm.router import LLMRouter, parse_llm_json, parse_lesson_json
from app.models.models import Assessment, LearningPath, PathLesson, Setting, User
from app.routers.lessons import normalize_llm_lesson
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


# Core generation logic shared by POST /generate, POST /advance, and the
# frontend's manual "generate from current level" — route handlers stay thin.
async def _generate_path_for_user(
    db: AsyncSession,
    current_user: User,
    assessment: Assessment | None = None,
) -> LearningPath:
    # Prefer the user's preferred CEFR level from settings over the stored
    # current_level — the latter may be stale if the assessment analysis failed.
    cefr_setting = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id, Setting.key == "default_cefr")
    )
    preferred = cefr_setting.scalar_one_or_none()
    preferred_level = (preferred.value or "").strip().upper() if preferred else ""
    current_level = preferred_level if preferred_level else (current_user.current_level or "A1")
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
    # Retry up to 3 times — smaller/flash models occasionally return blank or
    # truncated/malformed JSON for long structured prompts. A response is only
    # accepted once it parses into at least one usable lesson object, so a
    # malformed attempt is retried instead of failing the whole request.
    for attempt in range(3):
        try:
            result = await llm.complete_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You are an expert English curriculum designer. Return valid JSON only.",
                task="lesson",
                temperature=0.5,
                # 20+ lessons with descriptions are token-heavy; 4000 truncated
                # mid-lesson in production, leaving unparseable JSON.
                max_tokens=8000,
            )
        except Exception as e:
            logger.error(f"Learning path LLM call attempt {attempt + 1}/3 failed for user {current_user.id}: {e}")
            if attempt < 2:
                continue
            raise HTTPException(status_code=503, detail="Failed to generate the learning path. Please try again.")

        raw_content = result.get("content", "")
        provider_used = result.get("provider", "unknown")
        model_used = result.get("model", "unknown")
        logger.info(
            f"Learning path raw LLM response for user {current_user.id} "
            f"(provider={provider_used}, model={model_used}, attempt={attempt + 1}/3, length={len(raw_content)}): "
            f"{raw_content[:2000]}"
        )

        if not raw_content.strip():
            logger.warning(f"Learning path LLM returned empty content (attempt {attempt + 1}/3)")
            continue

        try:
            parsed = parse_llm_json(raw_content)
        except Exception as e:
            logger.error(f"Learning path JSON parsing failed for user {current_user.id} (attempt {attempt + 1}/3): {e}")
            continue

        # Tolerate a bare lessons array — the model occasionally returns "[...]"
        # instead of {"lessons": [...]}.
        if isinstance(parsed, list):
            parsed = {"lessons": parsed}
        if not isinstance(parsed, dict):
            logger.error(
                f"Learning path parsed into unusable shape for user {current_user.id} "
                f"(attempt {attempt + 1}/3): {type(parsed).__name__}"
            )
            continue

        lessons_data = parsed.get("lessons") or parsed.get("items") or []
        # The LLM sometimes double-encodes the array as a JSON string.
        if isinstance(lessons_data, str):
            try:
                lessons_data = json.loads(lessons_data)
            except Exception:
                lessons_data = []
        if isinstance(lessons_data, dict):
            lessons_data = [lessons_data]
        # Drop non-object entries — a stray string/number in the array would
        # otherwise crash lesson persistence with an AttributeError (500).
        lessons_data = [lesson for lesson in (lessons_data or []) if isinstance(lesson, dict)]
        if lessons_data:
            break

        logger.warning(
            f"Learning path response had no usable lessons (attempt {attempt + 1}/3): "
            f"keys={list(parsed.keys())}, raw_tail=...{raw_content[-300:]}"
        )

    logger.info(
        f"Learning path parsed result for user {current_user.id}: "
        f"keys={list(parsed.keys()) if parsed else []}, path_title={parsed.get('path_title')}, "
        f"lessons_count={len(lessons_data)}"
    )

    if not lessons_data:
        logger.error(
            f"Generated learning path has no usable lessons for user {current_user.id} after 3 attempts. "
            f"Last parsed keys: {list(parsed.keys()) if parsed else []}"
        )
        raise HTTPException(status_code=503, detail="Failed to generate the learning path. Please try again.")

    # Keep counters and stored lessons consistent in both directions:
    # fewer returned than requested → shrink lessons_required so the path can
    # still advance; more returned → slice so progress can never exceed 100%.
    lessons_data = lessons_data[:lessons_required]
    lessons_required = len(lessons_data)

    # The partial unique index (uq_learning_paths_user_active, created at
    # startup) allows only one active path per user. Deactivation and insert
    # must live in the SAME savepoint: if a concurrent /generate slips a new
    # active row in between, this insert loses the race and the whole savepoint
    # rolls back — including the deactivations below — so the loser can safely
    # re-select and return the winner's path without orphaning it.
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


@router.post("/{path_id}/lessons/{lesson_id}/generate", response_model=PathLessonDetailResponse)
async def generate_path_lesson(
    path_id: str,
    lesson_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate (lazily, idempotently) the interactive content for a path lesson.

    The content is tailored to the lesson's type/topic, the path's level span
    and — when the path was built from an assessment — the learner's measured
    weaknesses. Re-calling this on a lesson that already has exercises returns
    the stored content unchanged."""
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
    if content.get("exercises"):
        return _lesson_detail_response(lesson, content)

    assessment_info = ""
    if path.assessment_id:
        a_result = await db.execute(
            select(Assessment).where(
                Assessment.id == path.assessment_id, Assessment.user_id == current_user.id
            )
        )
        assessment = a_result.scalar_one_or_none()
        if assessment:
            strengths = json.loads(assessment.strengths) if assessment.strengths else []
            weaknesses = json.loads(assessment.weaknesses) if assessment.weaknesses else []
            assessment_info = (
                "\nLearner assessment context (personalize toward the weaknesses):\n"
                f"Estimated level: {assessment.estimated_level or path.current_level}\n"
                f"Weaknesses: {', '.join(weaknesses) or 'N/A'}\n"
                f"Strengths: {', '.join(strengths) or 'N/A'}\n"
                f"Summary: {assessment.summary or 'N/A'}\n"
            )

    prompt = (
        f"Create a short interactive English lesson as JSON for a learner going from "
        f"{path.current_level} to {path.target_level}.\n"
        f"Lesson type: {lesson.lesson_type}\n"
        f"Topic: {lesson.topic}\n"
        f"Description: {lesson.description}\n"
        f"{assessment_info}\n"
        f"Return JSON with this EXACT structure (do not omit any field):\n"
        f'{{"title": "Lesson title", "explanation": "A clear 2-4 sentence explanation", '
        f'"examples": ["Example 1", "Example 2", "Example 3"], '
        f'"exercises": [{{"question": "Fill in the blank: ...", "type": "fill_blank", "answer": "correct answer", "options": ["option1", "option2", "option3"]}}]}}\n'
        f"Include 3-5 exercises mixing fill_blank and multiple_choice (multiple_choice exercises "
        f"need 3-4 options and the answer must be one of them). "
        f"Adapt difficulty to {path.current_level}. Respond with ONLY valid JSON."
    )

    lesson_data: dict | None = None
    for attempt in range(3):
        try:
            llm = LLMRouter(db, current_user.id)
            result = await llm.complete_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=(
                    "You are an expert English teacher. Create focused, practical lessons "
                    "with exercises. Respond in valid JSON only. Never return empty arrays."
                ),
                task="lesson",
                temperature=0.5,
                max_tokens=1500,
            )
            lesson_data = parse_lesson_json(result["content"])
            if lesson_data.get("explanation") or lesson_data.get("exercises"):
                break
            logger.warning(
                f"Path lesson generation attempt {attempt + 1}/3 returned empty content (lesson {lesson.id})"
            )
            lesson_data = None
        except Exception as e:
            logger.error(f"Path lesson generation attempt {attempt + 1}/3 failed (lesson {lesson.id}): {e}")
            lesson_data = None

    if not lesson_data:
        raise HTTPException(status_code=503, detail="Failed to generate the lesson content. Please try again.")

    normalized = normalize_llm_lesson(lesson_data)
    if not normalized["exercises"]:
        raise HTTPException(status_code=503, detail="The generated lesson had no usable exercises. Try again.")

    content.update({
        "explanation": normalized["explanation"],
        "examples": normalized["examples"],
        "exercises": normalized["exercises"],
    })
    lesson.content = json.dumps(content, ensure_ascii=False)
    await db.flush()

    logger.info(f"Generated content for path lesson {lesson.id} ({lesson.lesson_type}: {lesson.topic})")
    return _lesson_detail_response(lesson, content)


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
    is_correct = body.answer.strip().lower() == correct_answer.strip().lower()
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

    await db.flush()
    # No db.expire(path) here — expired attribute access lazy-loads
    # synchronously outside the greenlet (MissingGreenlet). The stale object
    # is never read again; _generate_path_for_user returns a fresh path.

    # Generate the next path automatically — same shared service as
    # POST /generate, without assessment context (the old path is done).
    return await _generate_path_for_user(db, current_user)
