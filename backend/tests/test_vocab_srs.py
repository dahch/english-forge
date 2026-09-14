from datetime import date, datetime, timedelta, timezone
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import Base, ProgressDaily, User, VocabItem
from app.routers.vocab import get_due_cards, list_vocab, review_vocab
from app.schemas.vocab import ReviewSubmission
from app.srs.sm2 import next_review_date, sm2_update


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
    u = User(email="test@example.com", hashed_password="x", display_name="Test")
    db.add(u)
    await db.flush()
    await db.refresh(u)
    return u


def test_sm2_algorithm_calculations():
    # 1. Again (0): resets interval to 1, ease factor decreases by 0.2
    ef, interval = sm2_update(quality=0, ease_factor=2.5, interval=10)
    assert interval == 1
    assert ef == 2.3

    # 2. Hard (2): quality < 3 resets interval to 1, decreases EF
    ef, interval = sm2_update(quality=2, ease_factor=2.3, interval=6)
    assert interval == 1
    assert ef == 2.1

    # 3. Good (4): standard progression
    # Interval 0 -> 1
    ef, interval = sm2_update(quality=4, ease_factor=2.5, interval=0)
    assert interval == 1
    assert ef == 2.5  # 2.5 + (0.1 - (1)*(0.08 + 0.02)) = 2.5

    # Interval 1 -> 6
    ef, interval = sm2_update(quality=4, ease_factor=2.5, interval=1)
    assert interval == 6
    assert ef == 2.5

    # Interval 6 -> 6 * 2.5 = 15
    ef, interval = sm2_update(quality=4, ease_factor=2.5, interval=6)
    assert interval == 15

    # 4. Easy (5): bonus progression and EF increases
    ef, interval = sm2_update(quality=5, ease_factor=2.5, interval=1)
    assert interval == 6
    assert round(ef, 2) == 2.6  # 2.5 + 0.1 = 2.6


@pytest.mark.asyncio
async def test_get_due_cards_includes_past_and_null_dates(db: AsyncSession, user: User):
    today = date.today()
    past = today - timedelta(days=2)
    future = today + timedelta(days=3)

    card_null = VocabItem(
        user_id=user.id,
        word="ubiquitous",
        definition="present everywhere",
        next_review_at=None,
        interval_days=0,
    )
    card_past = VocabItem(
        user_id=user.id,
        word="ephemeral",
        definition="lasting for a short time",
        next_review_at=past,
        interval_days=1,
    )
    card_today = VocabItem(
        user_id=user.id,
        word="pragmatic",
        definition="dealing with things sensibly",
        next_review_at=today,
        interval_days=2,
    )
    card_future = VocabItem(
        user_id=user.id,
        word="serendipity",
        definition="pleasant surprise",
        next_review_at=future,
        interval_days=5,
    )

    db.add_all([card_null, card_past, card_today, card_future])
    await db.flush()

    due = await get_due_cards(limit=20, current_user=user, db=db)
    due_words = [d.word for d in due]

    # Should include null, past, today
    assert "ubiquitous" in due_words
    assert "ephemeral" in due_words
    assert "pragmatic" in due_words
    # Should NOT include future
    assert "serendipity" not in due_words
    assert len(due) == 3


@pytest.mark.asyncio
async def test_review_vocab_updates_item_and_progress_daily(db: AsyncSession, user: User):
    today = date.today()
    item = VocabItem(
        user_id=user.id,
        word="eloquent",
        definition="fluent or persuasive in speaking or writing",
        ease_factor=2.5,
        interval_days=1,
        next_review_at=today,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)

    # Perform a Good review (quality=4)
    res = await review_vocab(
        vocab_id=item.id,
        body=ReviewSubmission(quality=4),
        current_user=user,
        db=db,
    )

    assert res.interval_days == 6
    assert res.next_review_at == today + timedelta(days=6)
    assert res.ease_factor == 2.5
    assert res.last_reviewed_at is not None

    # Check ProgressDaily reviews_done incremented to 1
    prog_result = await db.execute(
        select(ProgressDaily).where(
            ProgressDaily.user_id == user.id,
            ProgressDaily.date == today,
        )
    )
    progress = prog_result.scalar_one_or_none()
    assert progress is not None
    assert progress.reviews_done == 1

    # Perform another review on same day
    await review_vocab(
        vocab_id=item.id,
        body=ReviewSubmission(quality=5),
        current_user=user,
        db=db,
    )

    prog_result2 = await db.execute(
        select(ProgressDaily).where(
            ProgressDaily.user_id == user.id,
            ProgressDaily.date == today,
        )
    )
    progress2 = prog_result2.scalar_one_or_none()
    assert progress2.reviews_done == 2


@pytest.mark.asyncio
async def test_list_vocab_filters(db: AsyncSession, user: User):
    today = date.today()
    item_new = VocabItem(
        user_id=user.id,
        word="lucid",
        definition="clear",
        interval_days=0,
        next_review_at=today,
    )
    item_due = VocabItem(
        user_id=user.id,
        word="cogent",
        definition="convincing",
        interval_days=3,
        next_review_at=today - timedelta(days=1),
    )
    item_future = VocabItem(
        user_id=user.id,
        word="tenacious",
        definition="persistent",
        interval_days=10,
        next_review_at=today + timedelta(days=7),
    )

    db.add_all([item_new, item_due, item_future])
    await db.flush()

    all_items = await list_vocab(filter="all", current_user=user, db=db)
    assert len(all_items) == 3

    due_items = await list_vocab(filter="due", current_user=user, db=db)
    due_words = [i.word for i in due_items]
    assert "lucid" in due_words
    assert "cogent" in due_words
    assert "tenacious" not in due_words

    new_items = await list_vocab(filter="new", current_user=user, db=db)
    assert len(new_items) == 1
    assert new_items[0].word == "lucid"


@pytest.mark.asyncio
async def test_generate_quiz_word_boundary_regex(db: AsyncSession, user: User):
    from app.routers.vocab import generate_quiz

    item1 = VocabItem(
        user_id=user.id,
        word="cat",
        definition="a small domesticated carnivorous mammal",
        example="The caterpillar saw the Cat sleeping peacefully.",
    )
    item2 = VocabItem(
        user_id=user.id,
        word="dog",
        definition="a domesticated carnivorous mammal",
        example="The dog barked at the mail carrier.",
    )
    db.add_all([item1, item2])
    await db.flush()

    questions = await generate_quiz(count=2, current_user=user, db=db)
    for q in questions:
        if q.question_type == "fill_blank" and q.correct_answer == "cat":
            assert "The caterpillar saw the ______ sleeping peacefully." in q.question
            assert "______erpillar" not in q.question


@pytest.mark.asyncio
async def test_dashboard_streak_single_query(db: AsyncSession, user: User):
    from app.routers.dashboard import get_stats

    today = date.today()
    for d in [0, 1, 2]:
        p = ProgressDaily(
            user_id=user.id,
            date=today - timedelta(days=d),
            minutes_spoken=10,
            reviews_done=5,
        )
        db.add(p)
    await db.flush()

    stats = await get_stats(current_user=user, db=db)
    assert stats["current_streak"] == 3

