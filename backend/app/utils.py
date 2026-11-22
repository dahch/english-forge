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


async def bump_daily_minutes(db: AsyncSession, user_id: str, minutes: int) -> None:
    """Increment today's ProgressDaily minutes_spoken counter for a user."""
    if minutes <= 0:
        return

    today = date.today()
    result = await db.execute(
        update(ProgressDaily)
        .where(ProgressDaily.user_id == user_id, ProgressDaily.date == today)
        .values(minutes_spoken=ProgressDaily.minutes_spoken + minutes)
    )
    if result.rowcount:
        return

    try:
        async with db.begin_nested():
            db.add(ProgressDaily(user_id=user_id, date=today, minutes_spoken=minutes))
    except IntegrityError:
        await db.execute(
            update(ProgressDaily)
            .where(ProgressDaily.user_id == user_id, ProgressDaily.date == today)
            .values(minutes_spoken=ProgressDaily.minutes_spoken + minutes)
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
        "tts_provider": profile.tts_provider,
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


# Voice provider alternatives. The first entry of each pair is the default and
# keeps the behaviour that existed before providers were selectable, so an
# unconfigured user (or a row written by an older version) never changes.
TTS_PROVIDERS = ("pockettts", "cartesia")
STT_PROVIDERS = ("moonshine", "cartesia")
DEFAULT_TTS_PROVIDER = TTS_PROVIDERS[0]
DEFAULT_STT_PROVIDER = STT_PROVIDERS[0]


async def _setting_value(db: AsyncSession, user_id: str, key: str) -> str | None:
    result = await db.execute(
        select(Setting).where(Setting.user_id == user_id, Setting.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


def _normalize_provider(value: str | None, supported: tuple[str, ...]) -> str:
    """Coerce a stored provider to a supported one, falling back to the default.

    Settings are free-form strings in the DB, so a value from an older version
    (or a typo written straight into SQLite) must not break synthesis. An
    unknown value silently means "use the default" rather than raising on a
    path that users cannot fix from the UI.
    """
    return value if value in supported else supported[0]


async def resolve_tts_provider(
    db: AsyncSession, user_id: str, profile: dict | None = None
) -> str:
    """Resolve which TTS provider speaks for the tutor.

    Priority mirrors resolve_tts_voice: tutor profile > settings > default.
    The tutor profile wins because the tutor's own voice is what the user hears
    during conversations, and the Settings page exposes it next to that voice.
    """
    provider = (profile or {}).get("tts_provider")
    if not provider:
        provider = await _setting_value(db, user_id, "tts_provider")
    return _normalize_provider(provider, TTS_PROVIDERS)


async def resolve_stt_provider(db: AsyncSession, user_id: str) -> str:
    """Resolve which STT provider transcribes the user's speech.

    There is no per-tutor STT (recognition is of the learner, not the tutor), so
    this reads the general preference only.
    """
    return _normalize_provider(
        await _setting_value(db, user_id, "stt_provider"), STT_PROVIDERS
    )
