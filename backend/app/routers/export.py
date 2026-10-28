"""Full user-data export.

One JSON document with every user-owned row, for backups and portability.
Provider API keys are deliberately excluded (only `has_api_key`), and message
audio (base64 data URIs) is replaced by a boolean: an export must never leak
credentials or balloon to hundreds of megabytes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import (
    Assessment,
    GeneratedLesson,
    LearningPath,
    LibraryLessonCompletion,
    Message,
    ProgressDaily,
    ProviderConfig,
    Scenario,
    Session,
    Setting,
    TutorProfile,
    User,
    VocabItem,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _iso(value: datetime | None) -> str | None:
    """Serialize a naive UTC datetime as RFC 3339 with an explicit Z.

    The API historically emitted naive timestamps; an export consumed by other
    tools must be unambiguous.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@router.get("/export")
async def export_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions_result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .options(selectinload(Session.messages).selectinload(Message.corrections))
        .order_by(Session.started_at)
    )
    sessions = sessions_result.scalars().all()

    vocab_result = await db.execute(
        select(VocabItem).where(VocabItem.user_id == current_user.id).order_by(VocabItem.created_at)
    )
    assessments_result = await db.execute(
        select(Assessment)
        .where(Assessment.user_id == current_user.id)
        .options(selectinload(Assessment.messages))
        .order_by(Assessment.started_at)
    )
    lessons_result = await db.execute(
        select(GeneratedLesson)
        .where(GeneratedLesson.user_id == current_user.id)
        .order_by(GeneratedLesson.created_at)
    )
    paths_result = await db.execute(
        select(LearningPath)
        .where(LearningPath.user_id == current_user.id)
        .options(selectinload(LearningPath.path_lessons))
        .order_by(LearningPath.created_at)
    )
    progress_result = await db.execute(
        select(ProgressDaily)
        .where(ProgressDaily.user_id == current_user.id)
        .order_by(ProgressDaily.date)
    )
    settings_result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id).order_by(Setting.key)
    )
    providers_result = await db.execute(
        select(ProviderConfig)
        .where(ProviderConfig.user_id == current_user.id)
        .order_by(ProviderConfig.priority.desc())
    )
    scenarios_result = await db.execute(
        select(Scenario).where(Scenario.user_id == current_user.id).order_by(Scenario.name)
    )
    completions_result = await db.execute(
        select(LibraryLessonCompletion).where(
            LibraryLessonCompletion.user_id == current_user.id
        )
    )
    tutor_result = await db.execute(
        select(TutorProfile).where(TutorProfile.user_id == current_user.id)
    )
    tutor = tutor_result.scalar_one_or_none()

    payload = {
        "format": "englishforge-export",
        "version": 1,
        "exported_at": _iso(datetime.now(timezone.utc)),
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "display_name": current_user.display_name,
            "current_level": current_user.current_level,
            "assessment_completed": current_user.assessment_completed,
            "created_at": _iso(current_user.created_at),
        },
        "settings": {s.key: s.value for s in settings_result.scalars().all()},
        "tutor_profile": (
            {
                "name": tutor.name,
                "age": tutor.age,
                "gender": tutor.gender,
                "personality": tutor.personality,
                "voice": tutor.voice,
            }
            if tutor
            else None
        ),
        "providers": [
            {
                "provider_name": p.provider_name,
                "base_url": p.base_url,
                "model": p.model,
                "protocol": p.protocol,
                "is_active": p.is_active,
                "priority": p.priority,
                "task_routing": p.task_routing,
                # Never export the secret itself.
                "has_api_key": bool(p.api_key_enc),
            }
            for p in providers_result.scalars().all()
        ],
        "scenarios": [
            {
                "id": s.id,
                "name": s.name,
                "system_prompt": s.system_prompt,
                "cefr_level": s.cefr_level,
                "is_custom": s.is_custom,
                "created_at": _iso(s.created_at),
            }
            for s in scenarios_result.scalars().all()
        ],
        "sessions": [
            {
                "id": s.id,
                "scenario_id": s.scenario_id,
                "started_at": _iso(s.started_at),
                "ended_at": _iso(s.ended_at),
                "cefr_level": s.cefr_level,
                "provider_used": s.provider_used,
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "text": m.text,
                        "has_audio": bool(m.audio_url),
                        "created_at": _iso(m.created_at),
                        "corrections": [
                            {
                                "error_type": c.error_type,
                                "original_fragment": c.original_fragment,
                                "correction": c.correction,
                                "explanation": c.explanation,
                            }
                            for c in m.corrections
                        ],
                    }
                    for m in s.messages
                ],
            }
            for s in sessions
        ],
        "vocab_items": [
            {
                "word": v.word,
                "definition": v.definition,
                "example": v.example,
                "ipa": v.ipa,
                "ease_factor": v.ease_factor,
                "interval_days": v.interval_days,
                "next_review_at": v.next_review_at.isoformat() if v.next_review_at else None,
                "last_reviewed_at": _iso(v.last_reviewed_at),
                "created_at": _iso(v.created_at),
            }
            for v in vocab_result.scalars().all()
        ],
        "assessments": [
            {
                "id": a.id,
                "started_at": _iso(a.started_at),
                "completed_at": _iso(a.completed_at),
                "abandoned_at": _iso(a.abandoned_at),
                "phase": a.phase,
                "estimated_level": a.estimated_level,
                "confidence": a.confidence,
                "dimension_scores": a.dimension_scores,
                "strengths": a.strengths,
                "weaknesses": a.weaknesses,
                "recommendations": a.recommendations,
                "summary": a.summary,
                "messages": [
                    {
                        "role": m.role,
                        "kind": m.kind,
                        "text": m.text,
                        "metrics": m.metrics,
                        "item_id": m.item_id,
                        "created_at": _iso(m.created_at),
                    }
                    for m in a.messages
                ],
            }
            for a in assessments_result.scalars().all()
        ],
        "generated_lessons": [
            {
                "id": lesson.id,
                "title": lesson.title,
                "topic": lesson.topic,
                "level": lesson.level,
                "explanation": lesson.explanation,
                "examples": lesson.examples,
                "exercises": lesson.exercises,
                "based_on_errors": lesson.based_on_errors,
                "completed": lesson.completed,
                "completed_at": _iso(lesson.completed_at),
                "created_at": _iso(lesson.created_at),
            }
            for lesson in lessons_result.scalars().all()
        ],
        "library_completions": [
            {
                "lesson_id": c.lesson_id,
                "completed_at": _iso(c.completed_at),
            }
            for c in completions_result.scalars().all()
        ],
        "learning_paths": [
            {
                "id": p.id,
                "assessment_id": p.assessment_id,
                "current_level": p.current_level,
                "target_level": p.target_level,
                "lessons_required": p.lessons_required,
                "lessons_completed": p.lessons_completed,
                "is_active": p.is_active,
                "created_at": _iso(p.created_at),
                "completed_at": _iso(p.completed_at),
                "lessons": [
                    {
                        "id": l.id,
                        "lesson_type": l.lesson_type,
                        "topic": l.topic,
                        "description": l.description,
                        "content": l.content,
                        "order": l.order,
                        "completed": l.completed,
                        "completed_at": _iso(l.completed_at),
                    }
                    for l in p.path_lessons
                ],
            }
            for p in paths_result.scalars().all()
        ],
        "progress_daily": [
            {
                "date": e.date.isoformat(),
                "minutes_spoken": e.minutes_spoken,
                "new_words": e.new_words,
                "reviews_done": e.reviews_done,
                "lessons_completed": e.lessons_completed,
            }
            for e in progress_result.scalars().all()
        ],
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="englishforge-export-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )
