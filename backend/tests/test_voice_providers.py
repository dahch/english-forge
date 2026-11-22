"""Tests de la resolución de proveedores de voz (TTS/STT) por usuario.

Cubren el orden de prioridad (tutor profile > settings > default) y, sobre todo,
la retrocompatibilidad: un usuario sin configuración debe seguir obteniendo el
proveedor local de siempre.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.models import Base, Setting, TutorProfile, User
from app.utils import (
    DEFAULT_STT_PROVIDER,
    DEFAULT_TTS_PROVIDER,
    resolve_stt_provider,
    resolve_tts_provider,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user(db):
    u = User(email="voice@test.local", hashed_password="x", display_name="Voice Tester")
    db.add(u)
    await db.flush()
    return u


async def _set(db, user_id, key, value):
    db.add(Setting(user_id=user_id, key=key, value=value))
    await db.flush()


async def _profile(db, user_id, tts_provider):
    db.add(TutorProfile(user_id=user_id, name="Sarah", personality="friendly", tts_provider=tts_provider))
    await db.flush()


# --- defaults / retrocompatibilidad ----------------------------------------


@pytest.mark.asyncio
async def test_tts_provider_defaults_to_pockettts(db, user):
    """Sin configuración, el proveedor es el local de siempre."""
    assert await resolve_tts_provider(db, user.id) == DEFAULT_TTS_PROVIDER
    assert DEFAULT_TTS_PROVIDER == "pockettts"


@pytest.mark.asyncio
async def test_stt_provider_defaults_to_moonshine(db, user):
    assert await resolve_stt_provider(db, user.id) == DEFAULT_STT_PROVIDER
    assert DEFAULT_STT_PROVIDER == "moonshine"


# --- prioridad --------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_provider_from_settings(db, user):
    await _set(db, user.id, "tts_provider", "cartesia")
    assert await resolve_tts_provider(db, user.id) == "cartesia"


@pytest.mark.asyncio
async def test_tutor_profile_overrides_settings_for_tts(db, user):
    """El perfil del tutor gana: es la voz concreta que se escucha."""
    await _set(db, user.id, "tts_provider", "pockettts")
    await _profile(db, user.id, "cartesia")

    profile = {"tts_provider": "cartesia"}
    assert await resolve_tts_provider(db, user.id, profile) == "cartesia"


@pytest.mark.asyncio
async def test_profile_without_provider_inherits_settings(db, user):
    """tts_provider NULL en el perfil = heredar (filas antiguas)."""
    await _set(db, user.id, "tts_provider", "cartesia")
    await _profile(db, user.id, None)

    profile = {"tts_provider": None}
    assert await resolve_tts_provider(db, user.id, profile) == "cartesia"


@pytest.mark.asyncio
async def test_no_profile_falls_back_to_settings(db, user):
    await _set(db, user.id, "tts_provider", "cartesia")
    assert await resolve_tts_provider(db, user.id, None) == "cartesia"


@pytest.mark.asyncio
async def test_stt_provider_from_settings(db, user):
    await _set(db, user.id, "stt_provider", "cartesia")
    assert await resolve_stt_provider(db, user.id) == "cartesia"


# --- degradación ante valores inválidos -------------------------------------


@pytest.mark.asyncio
async def test_unknown_tts_provider_falls_back_to_default(db, user):
    """Un valor raro en la DB no debe romper la síntesis."""
    await _set(db, user.id, "tts_provider", "inventado")
    assert await resolve_tts_provider(db, user.id) == DEFAULT_TTS_PROVIDER


@pytest.mark.asyncio
async def test_unknown_stt_provider_falls_back_to_default(db, user):
    await _set(db, user.id, "stt_provider", "inventado")
    assert await resolve_stt_provider(db, user.id) == DEFAULT_STT_PROVIDER


@pytest.mark.asyncio
async def test_unknown_provider_in_profile_falls_back(db, user):
    profile = {"tts_provider": "sonic-de-la-abuela"}
    assert await resolve_tts_provider(db, user.id, profile) == DEFAULT_TTS_PROVIDER


@pytest.mark.asyncio
async def test_empty_string_setting_is_ignored(db, user):
    await _set(db, user.id, "tts_provider", "")
    assert await resolve_tts_provider(db, user.id) == DEFAULT_TTS_PROVIDER
