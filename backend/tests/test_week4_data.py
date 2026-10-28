"""Week-4 data durability: schema versioning, integrity constraints,
defensive JSON reads and honest counters."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.json_utils import as_dict_list, load_json
from app.main import BASELINE_REVISION, _ensure_alembic_baseline_sync, _ensure_indexes_sync
from app.models.models import Base, GeneratedLesson, ProgressDaily, User, VocabItem
from app.routers.lessons import list_generated_lessons
from app.routers.vocab import create_vocab
from app.schemas.vocab import VocabItemCreate


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
    u = User(email="week4@example.com", hashed_password="x", display_name="Week4")
    db.add(u)
    await db.flush()
    return u


# --- schema versioning --------------------------------------------------------

def test_alembic_baseline_is_stamped_once(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/baseline.db")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        _ensure_alembic_baseline_sync(conn)
        _ensure_alembic_baseline_sync(conn)

        versions = conn.execute(text("SELECT version_num FROM alembic_version")).all()
    engine.dispose()

    assert versions == [(BASELINE_REVISION,)]


def test_baseline_revision_file_exists():
    from pathlib import Path

    revision = Path(__file__).parent.parent / "alembic" / "versions" / "0001_baseline.py"
    assert revision.exists()
    assert BASELINE_REVISION in revision.read_text(encoding="utf-8")


# --- integrity ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_vocabulary_is_rejected_case_insensitively(
    db: AsyncSession, user: User
):
    created = await create_vocab(
        body=VocabItemCreate(word="Serendipity"), current_user=user, db=db
    )
    await db.commit()
    assert created.word == "Serendipity"

    with pytest.raises(HTTPException) as exc:
        await create_vocab(
            body=VocabItemCreate(word="serendipity"), current_user=user, db=db
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_database_check_constraint_rejects_bad_role(db: AsyncSession, user: User):
    from app.models.models import Message, Session

    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()

    db.add(Message(session_id=session.id, role="system", text="nope"))
    with pytest.raises(IntegrityError):
        await db.flush()


def test_startup_index_sync_skips_missing_tables():
    """Startup index creation must not fail on a database that predates a table."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        _ensure_indexes_sync(conn)  # no vocab/scenarios/assessments tables
    engine.dispose()


# --- defensive JSON -----------------------------------------------------------

def test_load_json_degrades_on_corruption():
    assert load_json("{not json", default=[], expected_type=list) == []
    assert load_json('{"a": 1}', default=[], expected_type=list) == []
    assert load_json("[1, 2]", default=[], expected_type=list) == [1, 2]
    assert as_dict_list('[{"q": 1}, 2, "x"]') == [{"q": 1}]


@pytest.mark.asyncio
async def test_corrupt_generated_lesson_does_not_break_listing(
    db: AsyncSession, user: User
):
    db.add(
        GeneratedLesson(
            user_id=user.id,
            title="Corrupt",
            topic="grammar",
            level="B1",
            explanation="x",
            examples="{broken",
            exercises='{"not": "a list"}',
        )
    )
    await db.flush()

    rows = await list_generated_lessons(current_user=user, db=db)
    assert rows[0]["examples"] == []
    assert rows[0]["exercises"] == []


# --- counters -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_creating_a_word_updates_the_daily_counter(db: AsyncSession, user: User):
    await create_vocab(
        body=VocabItemCreate(word="ephemeral"), current_user=user, db=db
    )
    await db.commit()

    count = await db.scalar(
        select(ProgressDaily.new_words).where(ProgressDaily.user_id == user.id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_word_does_not_double_count(db: AsyncSession, user: User):
    user_id = user.id
    await create_vocab(body=VocabItemCreate(word="resilient"), current_user=user, db=db)
    await db.commit()
    with pytest.raises(HTTPException):
        await create_vocab(body=VocabItemCreate(word="Resilient"), current_user=user, db=db)

    # The 409 handler rolls back, which expires ORM instances; query by id.
    count = await db.scalar(
        select(ProgressDaily.new_words).where(ProgressDaily.user_id == user_id)
    )
    assert count == 1
    stored = (
        await db.execute(select(VocabItem).where(VocabItem.user_id == user_id))
    ).scalars().all()
    assert len(stored) == 1
