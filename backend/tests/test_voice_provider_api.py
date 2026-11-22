"""Tests del contrato HTTP de los selectores de proveedor de voz.

Cubren lo que la UI necesita: que el PATCH de settings acepte `stt_provider` y
`tts_provider`, que el perfil del tutor exponga y acepte `tts_provider`, y que
un cliente antiguo (sin esos campos) siga funcionando igual.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.dependencies import get_current_user
from app.main import app
from app.models.models import Base, User


@pytest_asyncio.fixture
async def client_and_user():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        user = User(email="ui@test.local", hashed_password="x", display_name="UI Tester")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    async def _get_db():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def _get_user():
        async with factory() as s:
            from sqlalchemy import select

            res = await s.execute(select(User).where(User.id == user_id))
            return res.scalar_one()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _get_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


# --- settings ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_accept_providers(client_and_user):
    res = await client_and_user.patch(
        "/api/settings", json={"stt_provider": "cartesia", "tts_provider": "cartesia"}
    )
    assert res.status_code == 200

    values = {s["key"]: s["value"] for s in res.json()}
    assert values["stt_provider"] == "cartesia"
    assert values["tts_provider"] == "cartesia"


@pytest.mark.asyncio
async def test_settings_read_back_providers(client_and_user):
    await client_and_user.patch("/api/settings", json={"tts_provider": "cartesia"})
    res = await client_and_user.get("/api/settings")
    values = {s["key"]: s["value"] for s in res.json()}
    assert values["tts_provider"] == "cartesia"


@pytest.mark.asyncio
async def test_settings_reject_unknown_provider(client_and_user):
    """El Literal del schema evita guardar basura desde la UI."""
    res = await client_and_user.patch("/api/settings", json={"tts_provider": "inventado"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_legacy_settings_patch_still_works(client_and_user):
    """Un cliente antiguo (solo stt_mode) no debe romperse."""
    res = await client_and_user.patch("/api/settings", json={"stt_mode": "personal_api"})
    assert res.status_code == 200
    values = {s["key"]: s["value"] for s in res.json()}
    assert values["stt_mode"] == "personal_api"


# --- tutor profile ----------------------------------------------------------


@pytest.mark.asyncio
async def test_tutor_profile_defaults_to_null_provider(client_and_user):
    """Perfil recién creado: sin proveedor propio = hereda el general."""
    res = await client_and_user.get("/api/tutor-profile")
    assert res.status_code == 200
    assert res.json()["tts_provider"] is None


@pytest.mark.asyncio
async def test_tutor_profile_accepts_provider(client_and_user):
    res = await client_and_user.patch("/api/tutor-profile", json={"tts_provider": "cartesia"})
    assert res.status_code == 200
    assert res.json()["tts_provider"] == "cartesia"


@pytest.mark.asyncio
async def test_tutor_profile_clears_provider_with_null(client_and_user):
    """La UI manda null al elegir "inherit"; debe guardarse como NULL."""
    await client_and_user.patch("/api/tutor-profile", json={"tts_provider": "cartesia"})

    res = await client_and_user.patch("/api/tutor-profile", json={"tts_provider": None})
    assert res.status_code == 200
    assert res.json()["tts_provider"] is None


@pytest.mark.asyncio
async def test_tutor_profile_rejects_unknown_provider(client_and_user):
    res = await client_and_user.patch("/api/tutor-profile", json={"tts_provider": "inventado"})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_tutor_profile_keeps_voice_and_provider_independent(client_and_user):
    """Cambiar la voz no debe resetear el motor, ni al contrario."""
    await client_and_user.patch(
        "/api/tutor-profile", json={"voice": "cartesia-voice", "tts_provider": "cartesia"}
    )
    res = await client_and_user.patch("/api/tutor-profile", json={"voice": "otra-voz"})

    body = res.json()
    assert body["voice"] == "otra-voz"
    assert body["tts_provider"] == "cartesia"
