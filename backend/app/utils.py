from __future__ import annotations

from datetime import date

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ProgressDaily, Setting, TutorProfile


async def bump_daily_lessons(db: AsyncSession, user_id: str, delta: int = 1) -> None:
    """Increment (or decrement) today's ProgressDaily lesson counter for a user.

    The counter is updated atomically in SQL so concurrent lesson completions
    don't clobber each other via read-modify-write.
    """
    if delta == 0:
        return

    today = date.today()
    new_value = ProgressDaily.lessons_completed + delta
    result = await db.execute(
        update(ProgressDaily)
        .where(ProgressDaily.user_id == user_id, ProgressDaily.date == today)
        .values(lessons_completed=case((new_value < 0, 0), else_=new_value))
    )
    if result.rowcount or delta < 0:
        return

    # No row for today yet — insert one. A concurrent request may insert the
    # same row first; the unique constraint makes that safe, so fall back to
    # retrying the atomic update.
    try:
        async with db.begin_nested():
            db.add(ProgressDaily(user_id=user_id, date=today, lessons_completed=delta))
    except IntegrityError:
        await db.execute(
            update(ProgressDaily)
            .where(ProgressDaily.user_id == user_id, ProgressDaily.date == today)
            .values(lessons_completed=case((new_value < 0, 0), else_=new_value))
        )


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


async def resolve_tts_voice(db: AsyncSession, user_id: str, profile: dict | None = None) -> str | None:
    """Resolve the voice used when the tutor speaks.

    Priority: tutor profile voice > settings tts_voice > None (the TTS engine
    then falls back to TTS_DEFAULT_VOICE from the environment). Pass an
    already-loaded profile dict (see get_tutor_profile_dict) to avoid
    re-querying TutorProfile in the same request.
    """
    voice = (profile or {}).get("voice")
    if voice:
        return voice

    result = await db.execute(
        select(Setting).where(Setting.user_id == user_id, Setting.key == "tts_voice")
    )
    setting = result.scalar_one_or_none()
    if setting and setting.value:
        return setting.value

    return None
