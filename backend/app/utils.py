from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ProgressDaily, TutorProfile


async def bump_daily_lessons(db: AsyncSession, user_id: str, delta: int = 1) -> None:
    """Increment (or decrement) today's ProgressDaily lesson counter for a user."""
    if delta == 0:
        return

    today = date.today()
    result = await db.execute(
        select(ProgressDaily).where(
            ProgressDaily.user_id == user_id,
            ProgressDaily.date == today,
        )
    )
    progress = result.scalar_one_or_none()
    if progress:
        progress.lessons_completed = max(0, progress.lessons_completed + delta)
    elif delta > 0:
        progress = ProgressDaily(
            user_id=user_id,
            date=today,
            lessons_completed=delta,
        )
        db.add(progress)


async def get_tutor_profile_dict(db: AsyncSession, user_id: str) -> dict | None:
    """Load the user's tutor profile as a plain dict, or None if not configured."""
    result = await db.execute(select(TutorProfile).where(TutorProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if not profile:
        return None
    return {
        "name": profile.name,
        "age": profile.age,
        "gender": profile.gender,
        "personality": profile.personality,
        "voice": profile.voice,
    }
