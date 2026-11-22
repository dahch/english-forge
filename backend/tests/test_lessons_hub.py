import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import (
    Base,
    Correction,
    GeneratedLesson,
    LibraryLessonCompletion,
    Message,
    ProgressDaily,
    Session,
    User,
)
from app.routers.lessons import (
    FALLBACK_LESSONS_BY_LEVEL,
    LessonGenerateRequest,
    LibraryLessonCompleteRequest,
    complete_library_lesson,
    generate_lesson,
    get_lesson,
    list_completed_library_lessons,
    list_library,
)


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
    u = User(email="test_hub@example.com", hashed_password="hashed_pw", display_name="HubStudent", current_level="B1")
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_generate_lesson_custom_topic_and_level(db: AsyncSession, user: User):
    """Test generating a lesson with custom topic and level."""
    req = LessonGenerateRequest(topic="Job Interview Phrasal Verbs", level="B2")
    res = await generate_lesson(body=req, current_user=user, db=db)

    assert res["generated"] is True
    assert "Job Interview Phrasal Verbs" in res["title"]
    assert res["level"] == "B2"
    assert len(res["exercises"]) >= 3
    assert "exercises" in res
    # Ensure answer is stripped from exercises
    for ex in res["exercises"]:
        assert "answer" not in ex


@pytest.mark.asyncio
async def test_generate_lesson_no_errors_fallback_no_400(db: AsyncSession, user: User):
    """User without conversation errors should NOT get 400; core foundation lesson is generated."""
    res = await generate_lesson(body=None, current_user=user, db=db)

    assert res["generated"] is True
    assert res["level"] == user.current_level or "B1"
    assert len(res["exercises"]) >= 3
    assert res["explanation"] != ""


@pytest.mark.asyncio
async def test_generate_lesson_based_on_errors(db: AsyncSession, user: User):
    """User with conversation errors generates lesson based on those errors."""
    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()

    msg = Message(session_id=session.id, role="user", text="I go yesterday.")
    db.add(msg)
    await db.flush()

    corr = Correction(
        message_id=msg.id,
        error_type="verb_tense",
        original_fragment="go",
        correction="went",
        explanation="Use past tense for completed actions.",
    )
    db.add(corr)
    await db.flush()

    res = await generate_lesson(body=None, current_user=user, db=db)
    assert res["generated"] is True
    assert "verb_tense" in (res.get("based_on_errors") or "")


@pytest.mark.asyncio
async def test_list_library_completion_status(db: AsyncSession, user: User):
    """list_library returns completed=True for completed library lessons."""
    # Initially none are completed
    lessons = await list_library(current_user=user, db=db)
    assert len(lessons) > 0
    assert all(l["completed"] is False for l in lessons)

    target_id = lessons[0]["id"]

    # Mark one completed
    await complete_library_lesson(
        lesson_id=target_id,
        body=LibraryLessonCompleteRequest(completed=True),
        current_user=user,
        db=db,
    )

    # Re-fetch library
    updated_lessons = await list_library(current_user=user, db=db)
    target_lesson = next(l for l in updated_lessons if l["id"] == target_id)
    assert target_lesson["completed"] is True


@pytest.mark.asyncio
async def test_complete_library_lesson_bumps_daily_lessons(db: AsyncSession, user: User):
    """Completing and un-completing a library lesson updates ProgressDaily."""
    lessons = await list_library(current_user=user, db=db)
    target_id = lessons[0]["id"]

    # Complete
    res = await complete_library_lesson(
        lesson_id=target_id,
        body=LibraryLessonCompleteRequest(completed=True),
        current_user=user,
        db=db,
    )
    assert res["completed"] is True

    # Check ProgressDaily
    progress = await db.execute(select(ProgressDaily).where(ProgressDaily.user_id == user.id))
    daily = progress.scalar_one_or_none()
    assert daily is not None
    assert daily.lessons_completed == 1

    # Uncomplete
    res2 = await complete_library_lesson(
        lesson_id=target_id,
        body=LibraryLessonCompleteRequest(completed=False),
        current_user=user,
        db=db,
    )
    assert res2["completed"] is False

    await db.refresh(daily)
    assert daily.lessons_completed == 0


@pytest.mark.asyncio
async def test_list_completed_library_lessons_endpoint(db: AsyncSession, user: User):
    """list_completed_library_lessons returns the list of completed IDs."""
    lessons = await list_library(current_user=user, db=db)
    l1 = lessons[0]["id"]
    l2 = lessons[1]["id"]

    await complete_library_lesson(lesson_id=l1, body=LibraryLessonCompleteRequest(completed=True), current_user=user, db=db)
    await complete_library_lesson(lesson_id=l2, body=LibraryLessonCompleteRequest(completed=True), current_user=user, db=db)

    completed_ids = await list_completed_library_lessons(current_user=user, db=db)
    assert set(completed_ids) == {l1, l2}


@pytest.mark.asyncio
async def test_complete_library_lesson_not_found(db: AsyncSession, user: User):
    """Marking non-existent library lesson returns 404."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await complete_library_lesson(
            lesson_id="non_existent_lesson_9999",
            body=LibraryLessonCompleteRequest(completed=True),
            current_user=user,
            db=db,
        )
    assert exc_info.value.status_code == 404
