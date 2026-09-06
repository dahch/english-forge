from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import Assessment, LearningPath, PathLesson, User
from app.utils import bump_daily_lessons

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning-paths", tags=["learning-paths"])


# Lessons required per CEFR jump — based on realistic Cambridge English estimates.
LEVEL_LESSONS_REQUIRED = {
    "A1": 18,
    "A2": 22,
    "B1": 28,
    "B2": 22,
    "C1": 12,
    "C2": 10,
}

NEXT_LEVEL = {
    "A1": "A2",
    "A2": "B1",
    "B1": "B2",
    "B2": "C1",
    "C1": "C2",
    "C2": None,
}


# Topic focus per CEFR jump — interpolated into the generation prompt so the
# lesson counts always match LEVEL_LESSONS_REQUIRED (single source of truth).
LEVEL_FOCUS = {
    "A1": "focused on fundamentals (basic tenses, everyday vocabulary, simple questions)",
    "A2": "with concentrated grammar (present perfect, conditionals type 1, modals, common phrasal verbs)",
    "B1": "— the densest level (conditionals 2/3, passive voice, reported speech, complex connectors, abstract vocabulary)",
    "B2": "focused on refinement (collocations, idioms, formal/informal register, nuance)",
    "C1": "— almost no new grammar, just polishing naturalness, cultural references, irony, and precision",
}


LESSON_TYPES = ["vocabulary", "grammar", "conversation", "listening", "reading", "writing"]


_LEARNING_PATH_FRAMEWORK = "\n".join(
    f"- {level}→{NEXT_LEVEL[level]}: {LEVEL_LESSONS_REQUIRED[level]} lessons {LEVEL_FOCUS[level]}."
    for level, nxt in NEXT_LEVEL.items()
    if nxt is not None
)

LEARNING_PATH_GENERATION_PROMPT = f"""You are an expert English curriculum designer and CEFR specialist. You are creating a personalized learning path for an adult English learner.

The learner has just completed a placement assessment.

Use the following CEFR framework for the learning path:
{_LEARNING_PATH_FRAMEWORK}

Each lesson should be one of these types: vocabulary, grammar, conversation, listening, reading, writing.

Distribute lesson types realistically:
- conversation: 40%
- grammar: 25%
- vocabulary: 20%
- listening: 10%
- reading/writing: 5%

Use the learner's weaknesses and recommendations to prioritize topics.

Return ONLY valid JSON in this exact format:
{{
  "path_title": "...",
  "description": "...",
  "lessons": [
    {{
      "lesson_type": "vocabulary|grammar|conversation|listening|reading|writing",
      "topic": "Specific topic name",
      "description": "What the learner will practice and why",
      "order": 1
    }}
  ]
}}

Lessons should be ordered from easier to harder within the target level. Make topics concrete and practical."""


class PathLessonResponse(BaseModel):
    id: str
    path_id: str
    lesson_type: str
    topic: str
    description: str
    content: str | None
    order: int
    completed: bool
    completed_at: datetime | None

    model_config = {"from_attributes": True}

    @field_validator("content", mode="before")
    @classmethod
    def parse_json_content(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
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


async def _deactivate_current_paths(db: AsyncSession, user_id: str) -> None:
    result = await db.execute(select(LearningPath).where(LearningPath.user_id == user_id, LearningPath.is_active == True))
    for path in result.scalars().all():
        path.is_active = False


# Serializable responses touch the `path_lessons` relationship — it must be
# eagerly loaded, otherwise Pydantic triggers a lazy load outside the
# greenlet context (MissingGreenlet) during response validation.
async def _get_path_with_lessons(db: AsyncSession, path_id: str) -> LearningPath:
    result = await db.execute(
        select(LearningPath)
        .where(LearningPath.id == path_id)
        .options(selectinload(LearningPath.path_lessons))
    )
    return result.scalar_one()


@router.get("/current", response_model=FullLearningPathResponse)
async def get_current_path(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LearningPath)
        .where(LearningPath.user_id == current_user.id, LearningPath.is_active == True)
        .order_by(LearningPath.created_at.desc())
        .options(selectinload(LearningPath.path_lessons))
    )
    path = result.scalar_one_or_none()
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

    current_level = current_user.current_level or "A1"
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
    try:
        result = await llm.complete_with_fallback(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are an expert English curriculum designer. Return valid JSON only.",
            task="lesson",
            temperature=0.5,
            max_tokens=2500,
        )
        parsed = parse_llm_json(result["content"])
    except Exception as e:
        logger.error(f"Learning path generation failed: {e}")
        raise HTTPException(status_code=503, detail=f"Failed to generate learning path: {e}")

    lessons_data = parsed.get("lessons", [])
    if not lessons_data:
        raise HTTPException(status_code=500, detail="Generated learning path has no lessons")

    # The LLM may return fewer lessons than requested — derive the target from
    # what we actually stored, otherwise the path can never be advanced.
    lessons_required = min(lessons_required, len(lessons_data))

    await _deactivate_current_paths(db, current_user.id)

    path = LearningPath(
        user_id=current_user.id,
        assessment_id=assessment.id if assessment else None,
        current_level=current_level,
        target_level=target_level,
        lessons_required=lessons_required,
        lessons_completed=0,
        is_active=True,
    )
    db.add(path)
    await db.flush()

    # lessons_required was capped above to len(lessons_data), so every
    # returned lesson is stored — no slicing needed.
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

    return await _get_path_with_lessons(db, path.id)


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

    # Core updates bypass the ORM identity map — expire so the re-select
    # below returns fresh counters and lesson states.
    db.expire(path)
    db.expire(lesson)

    return await _get_path_with_lessons(db, path.id)


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
    db.expire(path)

    # Generate next path automatically
    return await generate_path(GeneratePathRequest(), current_user, db)
