"""Offset pagination for the two collections that grow without bound."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import Base, GeneratedLesson, User, VocabItem
from app.routers.lessons import list_generated_lessons
from app.routers.vocab import list_vocab


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user(db: AsyncSession) -> User:
    u = User(email="pages@example.com", hashed_password="x", display_name="Pages")
    db.add(u)
    await db.flush()
    return u


def _days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.mark.asyncio
async def test_vocab_pagination_is_stable_and_ordered(db: AsyncSession, user: User):
    for index in range(5):
        db.add(
            VocabItem(
                user_id=user.id,
                word=f"word-{index}",
                created_at=_days_ago(5 - index),
            )
        )
    await db.flush()

    first = await list_vocab(
        filter="all", limit=2, offset=0, current_user=user, db=db
    )
    second = await list_vocab(
        filter="all", limit=2, offset=2, current_user=user, db=db
    )
    last = await list_vocab(filter="all", limit=2, offset=4, current_user=user, db=db)

    assert [item.word for item in first] == ["word-4", "word-3"]
    assert [item.word for item in second] == ["word-2", "word-1"]
    assert [item.word for item in last] == ["word-0"]


@pytest.mark.asyncio
async def test_vocab_pagination_applies_the_filter_before_the_page(
    db: AsyncSession, user: User
):
    db.add(VocabItem(user_id=user.id, word="old", interval_days=0, created_at=_days_ago(2)))
    db.add(VocabItem(user_id=user.id, word="new", interval_days=5, created_at=_days_ago(1)))
    await db.flush()

    new_only = await list_vocab(
        filter="new", limit=1, offset=0, current_user=user, db=db
    )
    assert [item.word for item in new_only] == ["old"]


@pytest.mark.asyncio
async def test_generated_lesson_pagination(db: AsyncSession, user: User):
    for index in range(4):
        db.add(
            GeneratedLesson(
                user_id=user.id,
                title=f"Lesson {index}",
                explanation="x",
                created_at=_days_ago(4 - index),
            )
        )
    await db.flush()

    page = await list_generated_lessons(
        limit=2, offset=1, current_user=user, db=db
    )
    assert [lesson["title"] for lesson in page] == ["Lesson 2", "Lesson 1"]
