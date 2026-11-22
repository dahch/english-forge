"""Provider connectivity probe and provider editing."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.integrations.crypto import encrypt_value
from app.llm.anthropic_adapter import AnthropicProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.models.models import Base, ProviderConfig, User
from app.rate_limit import reset_rate_limits
from app.routers.settings import (
    test_provider_draft as probe_draft_endpoint,
    test_provider_saved as probe_saved_endpoint,
    update_provider,
)
from app.schemas.settings import ProviderConfigUpdate, ProviderTestRequest
from app.services.provider_test import probe_provider


@pytest.fixture(autouse=True)
def _reset_limits():
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
    u = User(email="probe@example.com", hashed_password="x", display_name="Probe")
    db.add(u)
    await db.flush()
    return u


async def _provider(db: AsyncSession, user: User, **overrides) -> ProviderConfig:
    values = {
        "user_id": user.id,
        "provider_name": "openai",
        "api_key_enc": encrypt_value("sk-secret"),
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
    }
    values.update(overrides)
    config = ProviderConfig(**values)
    db.add(config)
    await db.flush()
    return config


@pytest.mark.asyncio
async def test_probe_success(monkeypatch):
    async def fake_complete(self, messages, system_prompt, **kwargs):
        return {"content": "OK", "model": "gpt-4.1-mini"}

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", fake_complete)

    result = await probe_provider(
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        model="gpt-4.1-mini",
    )
    assert result["ok"] is True
    assert result["reply"] == "OK"
    assert result["latency_ms"] is not None
    assert "sk-secret" not in str(result)


@pytest.mark.asyncio
async def test_probe_error_never_leaks_the_key(monkeypatch):
    async def failing(self, messages, system_prompt, **kwargs):
        raise RuntimeError("unauthorized for sk-secret")

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", failing)

    result = await probe_provider(
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        model="gpt-4.1-mini",
    )
    assert result["ok"] is False
    assert "sk-secret" not in result["error"]
    assert "***" in result["error"]


@pytest.mark.asyncio
async def test_probe_timeout(monkeypatch):
    async def slow(self, messages, system_prompt, **kwargs):
        await asyncio.sleep(1)
        return {"content": "late"}

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", slow)

    result = await probe_provider(
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        model="gpt-4.1-mini",
        timeout=0.05,
    )
    assert result["ok"] is False
    assert "No response" in result["error"]


@pytest.mark.asyncio
async def test_probe_empty_content_is_a_failure(monkeypatch):
    async def empty(self, messages, system_prompt, **kwargs):
        return {"content": "   ", "model": "m"}

    monkeypatch.setattr(OpenAICompatibleProvider, "complete", empty)
    result = await probe_provider(
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        model="m",
    )
    assert result["ok"] is False
    assert "empty content" in result["error"]


@pytest.mark.asyncio
async def test_anthropic_probe_uses_anthropic_adapter(monkeypatch):
    async def fake(self, messages, system_prompt, **kwargs):
        return {"content": "OK", "model": "claude"}

    monkeypatch.setattr(AnthropicProvider, "complete", fake)
    result = await probe_provider(
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant",
        model="claude",
    )
    assert result["ok"] is True
    assert result["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_saved_provider_probe_decrypts_key(db: AsyncSession, user: User, monkeypatch):
    config = await _provider(db, user)
    seen = {}

    async def fake_probe(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "provider": "openai", "reply": "OK", "latency_ms": 5}

    monkeypatch.setattr("app.routers.settings.probe_provider", fake_probe)
    response = await probe_saved_endpoint(
        provider_id=config.id, current_user=user, db=db
    )
    assert response.ok is True
    assert seen["api_key"] == "sk-secret"
    assert seen["base_url"] == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_saved_provider_with_undecryptable_key_returns_400(
    db: AsyncSession, user: User
):
    config = await _provider(db, user, api_key_enc="not-a-fernet-token")
    with pytest.raises(HTTPException) as exc:
        await probe_saved_endpoint(provider_id=config.id, current_user=user, db=db)
    assert exc.value.status_code == 400
    assert "decrypt" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_draft_probe_endpoint(db: AsyncSession, user: User, monkeypatch):
    async def fake_probe(**kwargs):
        return {"ok": True, "provider": "openai", "reply": "OK", "latency_ms": 3}

    monkeypatch.setattr("app.routers.settings.probe_provider", fake_probe)
    response = await probe_draft_endpoint(
        body=ProviderTestRequest(
            provider_name="draft",
            protocol="openai",
            base_url="https://example.com/v1",
            model="m",
            api_key="sk-draft",
        ),
        current_user=user,
    )
    assert response.ok is True


@pytest.mark.asyncio
async def test_rename_provider_and_keep_key(db: AsyncSession, user: User):
    config = await _provider(db, user)
    old_cipher = config.api_key_enc

    updated = await update_provider(
        provider_id=config.id,
        body=ProviderConfigUpdate(provider_name="my-openai", api_key=""),
        current_user=user,
        db=db,
    )
    assert updated.provider_name == "my-openai"
    assert updated.has_api_key is True
    assert config.api_key_enc == old_cipher  # blank API key means "keep"


@pytest.mark.asyncio
async def test_rename_provider_rejects_duplicates(db: AsyncSession, user: User):
    await _provider(db, user, provider_name="one")
    second = await _provider(db, user, provider_name="two")

    with pytest.raises(HTTPException) as exc:
        await update_provider(
            provider_id=second.id,
            body=ProviderConfigUpdate(provider_name="one"),
            current_user=user,
            db=db,
        )
    assert exc.value.status_code == 400
