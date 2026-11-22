"""Session finalisation: idempotent end-of-session billing.

The previous implementation recomputed `ended_at - started_at` on every
PATCH /sessions/{id}/end, so a double call (two tabs, a retried request, a
mobile client resuming) added the same minutes to ProgressDaily twice. It also
billed wall-clock time: a tab left open overnight counted as practice.

Rules implemented here:
- Ending an already-ended session never bills again.
- Billable time ends at the last message (+ a small grace), not at the moment
  the user clicks End, so idle time is not counted as speaking.
- The result is capped by MAX_SESSION_MINUTES so a forgotten tab cannot
  produce an absurd daily total.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import Message, Session
from app.utils import bump_daily_minutes

logger = logging.getLogger(__name__)


def _naive_utc(value: datetime | None) -> datetime | None:
    """Normalise to naive UTC (how SQLite returns DateTime columns)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def billable_minutes(db: AsyncSession, session: Session) -> float:
    settings = get_settings()
    started = _naive_utc(session.started_at)
    if started is None:
        return 0.0

    ended = _naive_utc(session.ended_at) or _naive_utc(datetime.now(timezone.utc))
    last_message = (
        await db.execute(
            select(func.max(Message.created_at)).where(Message.session_id == session.id)
        )
    ).scalar()
    last_message = _naive_utc(last_message)

    effective_end = ended
    if last_message is not None:
        effective_end = min(
            ended,
            last_message + timedelta(minutes=settings.SESSION_END_GRACE_MINUTES),
        )

    minutes = (effective_end - started).total_seconds() / 60.0
    return max(0.0, min(minutes, float(settings.MAX_SESSION_MINUTES)))


async def finalize_session(db: AsyncSession, session: Session) -> float:
    """End `session` if needed and bill its minutes exactly once."""
    if session.ended_at is not None:
        # Already finalised (e.g. by the WebSocket path) — do not bill again.
        return await billable_minutes(db, session)

    session.ended_at = datetime.now(timezone.utc)
    minutes = await billable_minutes(db, session)
    if minutes > 0:
        await bump_daily_minutes(db, session.user_id, int(round(minutes)))
    logger.info(
        "Session %s ended: %.1f billable minutes (billed=%s)",
        session.id,
        minutes,
        int(round(minutes)),
    )
    return minutes
