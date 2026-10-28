"""Week-1 hardening: secrets, rate limiting, assessment recovery, session
billing, turn persistence and data export.

Covers the audit findings:
- C1/C2: startup refuses weak JWT secrets and malformed Fernet keys.
- C3: failed logins are rate limited; emails are normalised; long passwords are
  rejected instead of being silently truncated by bcrypt.
- C5: an assessment can be abandoned (and reclaimed when stale) so the
  in-progress unique index never locks a user out.
- A1: a conversation turn persists correctly and does not fail when TTS is down.
- A11: ending a session twice bills its minutes exactly once.
- Export never leaks provider API keys.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.config import Settings
from app.integrations.crypto import encrypt_value
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.router import LLMRouter
from app.main import _ensure_indexes_sync
from app.models.models import (
    Assessment,
    Base,
    Message,
    ProgressDaily,
    ProviderConfig,
    Session,
    User,
    VocabItem,
)
from app.rate_limit import limiter, reset_rate_limits
from app.routers.assessment import (
    _abandon_stale_assessments,
    abandon_assessment,
    get_current_assessment,
    start_assessment,
)
from app.routers.auth import login, register
from app.routers.export import export_user_data
from app.routers.messages import send_message
from app.routers.sessions import end_session
from app.schemas.auth import UserLogin, UserRegister
from app.schemas.session import MessageCreate
from app.security import (
    hash_password,
    validate_security_settings,
    verify_password,
)


def _settings(**overrides) -> Settings:
    base = {"JWT_SECRET_KEY": "a" * 40, "SETTINGS_ENCRYPTION_KEY": ""}
    base.update(overrides)
    return Settings(**base)


def _request(ip: str = "203.0.113.7") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": (ip, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.fixture(autouse=True)
def _clean_limiter():
    reset_rate_limits()
    yield
    reset_rate_limits()


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
    u = User(
        email="week1@example.com",
        hashed_password=hash_password("correct horse battery"),
        display_name="Week One",
    )
    db.add(u)
    await db.flush()
    return u


# --- C1/C2: startup validation ------------------------------------------------

def test_missing_jwt_secret_aborts_startup():
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_security_settings(_settings(JWT_SECRET_KEY=""))


def test_repository_default_secret_aborts_startup():
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_security_settings(
            _settings(JWT_SECRET_KEY="change-me-in-production-use-openssl-rand-hex-32")
        )


def test_short_jwt_secret_aborts_startup():
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        validate_security_settings(_settings(JWT_SECRET_KEY="short"))


def test_strong_secret_passes():
    validate_security_settings(_settings(JWT_SECRET_KEY="f" * 64))


def test_insecure_override_allows_weak_secret():
    validate_security_settings(
        _settings(JWT_SECRET_KEY="", ALLOW_INSECURE_DEFAULTS=True)
    )


def test_malformed_encryption_key_aborts_startup():
    with pytest.raises(RuntimeError, match="SETTINGS_ENCRYPTION_KEY"):
        validate_security_settings(_settings(SETTINGS_ENCRYPTION_KEY="not-a-fernet-key"))


def test_valid_dedicated_encryption_key_passes():
    key = Fernet.generate_key().decode()
    validate_security_settings(_settings(SETTINGS_ENCRYPTION_KEY=key))


# --- C3: passwords and rate limiting ------------------------------------------

def test_password_over_72_bytes_is_rejected_not_truncated():
    with pytest.raises(ValidationError):
        UserRegister(email="a@example.com", password="a" * 80)
    with pytest.raises(ValueError, match="too long"):
        hash_password("a" * 80)


def test_bcrypt_verification_tolerates_corrupt_hash():
    assert verify_password("whatever", "not-a-bcrypt-hash") is False


@pytest.mark.asyncio
async def test_registration_normalises_email(db: AsyncSession):
    body = UserRegister(email="  MiXeD@Example.COM ", password="long-enough-pw")
    created = await register(body=body, request=_request(), db=db)
    assert created.email == "mixed@example.com"


@pytest.mark.asyncio
async def test_failed_logins_are_rate_limited(db: AsyncSession, user: User):
    body = UserLogin(email="WEEK1@example.com", password="wrong-password")

    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            await login(body=body, request=_request(), db=db)
        assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await login(body=body, request=_request(), db=db)
    assert exc.value.status_code == 429
    assert exc.value.headers and "Retry-After" in exc.value.headers


@pytest.mark.asyncio
async def test_successful_login_clears_email_budget(db: AsyncSession, user: User):
    bad = UserLogin(email=user.email, password="wrong-password")
    for _ in range(4):
        with pytest.raises(HTTPException):
            await login(body=bad, request=_request(), db=db)

    ok = await login(
        body=UserLogin(email=user.email, password="correct horse battery"),
        request=_request(),
        db=db,
    )
    assert ok.access_token

    # Budget was reset: four more failures are still 401, not 429.
    for _ in range(4):
        with pytest.raises(HTTPException) as exc:
            await login(body=bad, request=_request(), db=db)
        assert exc.value.status_code == 401


def test_limiter_window_expires():
    assert limiter.check("k", 2, 0.05) is None
    limiter.record("k", 0.05)
    limiter.record("k", 0.05)
    assert limiter.check("k", 2, 0.05) is not None
    limiter.clear("k")
    assert limiter.check("k", 2, 0.05) is None


# --- C5: abandoning / reclaiming assessments ----------------------------------

@pytest.mark.asyncio
async def test_abandon_frees_the_in_progress_slot(db: AsyncSession, user: User):
    first = await start_assessment(response=Response(), current_user=user, db=db)
    await db.commit()

    await abandon_assessment(assessment_id=first.id, current_user=user, db=db)
    await db.commit()

    stored = await db.get(Assessment, first.id)
    assert stored is not None and stored.abandoned_at is not None

    # Abandoning twice is idempotent.
    await abandon_assessment(assessment_id=first.id, current_user=user, db=db)

    second = await start_assessment(response=Response(), current_user=user, db=db)
    await db.commit()
    assert second.id != first.id
    assert second.phase == "mic_check"


@pytest.mark.asyncio
async def test_abandoned_assessment_is_hidden_from_current(db: AsyncSession, user: User):
    started = await start_assessment(response=Response(), current_user=user, db=db)
    await db.commit()
    await abandon_assessment(assessment_id=started.id, current_user=user, db=db)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await get_current_assessment(current_user=user, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_completed_assessment_cannot_be_abandoned(db: AsyncSession, user: User):
    assessment = Assessment(
        user_id=user.id,
        completed_at=datetime.now(timezone.utc),
        estimated_level="B1",
    )
    db.add(assessment)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await abandon_assessment(
            assessment_id=assessment.id, current_user=user, db=db
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_stale_assessment_is_reclaimed(db: AsyncSession, user: User):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    assessment = Assessment(user_id=user.id, started_at=old, phase="conversation")
    db.add(assessment)
    await db.flush()

    reclaimed = await _abandon_stale_assessments(db, user.id)
    await db.commit()

    assert reclaimed == 1
    # The bulk UPDATE bypasses the ORM identity map; re-read the row.
    await db.refresh(assessment)
    assert assessment.abandoned_at is not None

    fresh = await start_assessment(response=Response(), current_user=user, db=db)
    assert fresh.id != assessment.id


# --- A1: conversation turn persistence ---------------------------------------

def _fake_llm(monkeypatch, payload: dict):
    async def fake_complete(self, messages, system_prompt, task="conversation", **kwargs):
        return {
            "content": json.dumps(payload),
            "provider": "mock",
            "model": "mock-model",
        }

    monkeypatch.setattr(LLMRouter, "complete_with_fallback", fake_complete)


@pytest.mark.asyncio
async def test_turn_persists_in_order_and_dedupes_vocab(
    db: AsyncSession, user: User, monkeypatch
):
    _fake_llm(
        monkeypatch,
        {
            "reply": "Nice to meet you!",
            "corrections": [
                {
                    "error_type": "grammar",
                    "original": "I go yesterday",
                    "correction": "I went yesterday",
                    "explanation": "Past tense.",
                }
            ],
            "new_vocab": [
                {"word": "serendipity", "definition": "lucky discovery", "example": "Pure serendipity."}
            ],
        },
    )

    async def tts_down(self, text, voice=None):
        raise RuntimeError("personal-api down")

    monkeypatch.setattr(TTSPersonalAPI, "synthesize", tts_down)

    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()

    turn = await send_message(
        session_id=session.id,
        body=MessageCreate(text="Hello tutor"),
        current_user=user,
        db=db,
    )

    assert turn.assistant_message.text == "Nice to meet you!"
    assert turn.user_message.created_at < turn.assistant_message.created_at
    assert len(turn.corrections) == 1
    assert turn.corrections[0].id and turn.corrections[0].correction == "I went yesterday"
    assert turn.audio_url is None  # TTS failure must not fail the turn

    # A second turn with the same vocabulary must not duplicate the row.
    await send_message(
        session_id=session.id,
        body=MessageCreate(text="Again"),
        current_user=user,
        db=db,
    )
    count = await db.scalar(
        select(func.count(VocabItem.id)).where(VocabItem.user_id == user.id)
    )
    assert count == 1


@pytest.mark.asyncio
async def test_empty_message_rejected(db: AsyncSession, user: User):
    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()
    with pytest.raises(HTTPException) as exc:
        await send_message(
            session_id=session.id,
            body=MessageCreate(text="   "),
            current_user=user,
            db=db,
        )
    assert exc.value.status_code == 400


# --- A11: session end billing -------------------------------------------------

@pytest.mark.asyncio
async def test_end_session_bills_minutes_once(db: AsyncSession, user: User):
    started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    last_msg = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    session = Session(user_id=user.id, cefr_level="B1", started_at=started)
    db.add(session)
    await db.flush()
    db.add(Message(session_id=session.id, role="user", text="hi", created_at=last_msg))
    await db.flush()

    first = await end_session(session_id=session.id, current_user=user, db=db)
    await db.commit()

    # start → last message + 2 min grace = 22 minutes.
    assert first.duration_minutes == pytest.approx(22.0, abs=0.1)

    second = await end_session(session_id=session.id, current_user=user, db=db)
    await db.commit()
    assert second.duration_minutes == first.duration_minutes

    billed = await db.scalar(
        select(ProgressDaily.minutes_spoken).where(ProgressDaily.user_id == user.id)
    )
    assert billed == 22


@pytest.mark.asyncio
async def test_end_session_caps_wall_clock(db: AsyncSession, user: User):
    started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=10)
    session = Session(user_id=user.id, cefr_level="B1", started_at=started)
    db.add(session)
    await db.flush()

    summary = await end_session(session_id=session.id, current_user=user, db=db)
    assert summary.duration_minutes == pytest.approx(180.0, abs=0.1)


# --- export -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_never_contains_api_keys(db: AsyncSession, user: User):
    db.add(
        ProviderConfig(
            user_id=user.id,
            provider_name="openai",
            api_key_enc=encrypt_value("sk-super-secret"),
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
        )
    )
    db.add(Message(session_id=(await _session(db, user)).id, role="user", text="hi"))
    await db.flush()

    response = await export_user_data(current_user=user, db=db)
    body = response.body.decode()

    assert "sk-super-secret" not in body
    payload = json.loads(body)
    assert payload["providers"][0]["has_api_key"] is True
    assert payload["format"] == "englishforge-export"
    assert payload["sessions"]


async def _session(db: AsyncSession, user: User) -> Session:
    session = Session(user_id=user.id, cefr_level="B1")
    db.add(session)
    await db.flush()
    return session


# --- startup index migration --------------------------------------------------

def test_outdated_assessment_index_is_replaced():
    """The abandon feature needs the unique index to ignore abandoned rows.

    Existing databases still carry the old predicate (`completed_at IS NULL`
    only). Startup must detect the definition change, replace the index and stay
    idempotent on the next boot.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE assessments (id TEXT PRIMARY KEY, user_id TEXT, "
                "completed_at DATETIME, abandoned_at DATETIME)"
            )
        )
        conn.execute(
            text("CREATE TABLE learning_paths (id TEXT PRIMARY KEY, user_id TEXT, is_active BOOLEAN)")
        )
        conn.execute(
            text(
                "CREATE TABLE assessment_messages (id TEXT PRIMARY KEY, assessment_id TEXT, "
                "item_id TEXT, role TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX uq_assessments_user_in_progress "
                "ON assessments (user_id) WHERE completed_at IS NULL"
            )
        )

        _ensure_indexes_sync(conn)
        stored = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_assessments_user_in_progress'")
        ).scalar()
        assert "abandoned_at" in stored

        # Second boot: definition already matches; must be a no-op that does not
        # fail (a needless drop/recreate would still work, but the comparison in
        # _normalize_sql is what prevents it).
        _ensure_indexes_sync(conn)
        again = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_assessments_user_in_progress'")
        ).scalar()
        assert again == stored
    engine.dispose()
