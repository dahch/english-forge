"""Refresh-token rotation and revocation (ADR-022).

Rules:
- The raw token is never stored; only its SHA-256.
- Every refresh rotates: the presented token is revoked and a new one issued.
- Presenting an already-revoked token means it leaked (or a race): revoke every
  session for that user and bump `token_version`, which invalidates their
  outstanding access tokens immediately.
- Tokens are capped per user and expired rows are pruned on each issue.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.models import RefreshToken, User
from app.rate_limit import client_ip

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC — matches how SQLite stores DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _client_metadata(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    user_agent = request.headers.get("user-agent")
    return (
        (user_agent[:300] if user_agent else None),
        client_ip(request)[:64],
    )


async def _prune(db: AsyncSession, user_id: str) -> None:
    settings = get_settings()
    now = _utcnow()
    await db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.expires_at < now
        )
    )
    # Keep at most MAX_REFRESH_TOKENS_PER_USER active devices; revoke the oldest.
    rows = await db.execute(
        select(RefreshToken.id)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .order_by(RefreshToken.created_at.desc())
        .offset(settings.MAX_REFRESH_TOKENS_PER_USER)
    )
    stale_ids = [row[0] for row in rows.all()]
    if stale_ids:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.id.in_(stale_ids))
            .values(revoked_at=now)
        )


async def issue_refresh_token(
    db: AsyncSession, user: User, request: Request | None = None
) -> str:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    user_agent, ip_address = _client_metadata(request)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=_utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    await db.flush()
    await _prune(db, user.id)
    return raw_token


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(raw_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_utcnow())
    )


async def revoke_all_refresh_tokens(
    db: AsyncSession, user_id: str, *, bump_token_version: bool = False
) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_utcnow())
    )
    if bump_token_version:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(token_version=User.token_version + 1)
        )


async def rotate_refresh_token(
    db: AsyncSession, raw_token: str, request: Request | None = None
) -> tuple[User, str]:
    """Validate + rotate a refresh token. Returns (user, new_raw_token)."""
    row = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(raw_token)
            )
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        )

    if row.revoked_at is not None:
        # Reuse of a rotated token: assume theft and cut every session.
        await revoke_all_refresh_tokens(db, row.user_id, bump_token_version=True)
        await db.commit()
        logger.warning(
            "Refresh token reuse detected for user %s — all sessions revoked",
            row.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Sign in again.",
        )

    if row.expires_at <= _utcnow():
        row.revoked_at = _utcnow()
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Sign in again.",
        )

    user = await db.get(User, row.user_id)
    if user is None:
        row.revoked_at = _utcnow()
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )

    row.revoked_at = _utcnow()
    new_token = await issue_refresh_token(db, user, request)
    return user, new_token
