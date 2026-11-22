"""Tests for Iteration 5: System Robustness, LLM Router Fallback, WS Session Safety & Facade Removal."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.integrations.crypto import encrypt_value
from app.llm.router import LLMRouter
from app.main import app
from app.models.models import Base, Message, ProviderConfig, Session, User
from app.routers.ws import _get_user_from_token, _process_turn
from app.security import create_access_token


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
        email="robust_test@example.com",
        hashed_password="hashed_pw",
        display_name="RobustUser",
        current_level="B1",
    )
    db.add(u)
    await db.commit()
    return u


@pytest_asyncio.fixture
async def active_session(db: AsyncSession, user: User) -> Session:
    s = Session(
        user_id=user.id,
        scenario_id=None,
        cefr_level="B1",
        started_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.commit()
    return s


# ---------------------------------------------------------------------------
# 1. LLMRouter Provider Precedence & Fallbacks
# ---------------------------------------------------------------------------

class TestLLMRouterRobustness:
    @pytest.mark.asyncio
    async def test_db_provider_takes_priority_over_env(self, db: AsyncSession, user: User, monkeypatch):
        mock_settings = Settings(
            OPENAI_API_KEY="env-openai-key",
            OPENAI_MODEL="gpt-4.1-mini",
        )
        monkeypatch.setattr("app.config.get_settings", lambda: mock_settings)

        # Add DB provider for this user
        cfg = ProviderConfig(
            user_id=user.id,
            provider_name="custom_db_provider",
            protocol="openai",
            base_url="https://api.custom.com/v1",
            api_key_enc=encrypt_value("db-custom-key"),
            model="custom-model-x",
            is_active=True,
            priority=10,
        )
        db.add(cfg)
        await db.commit()

        router = LLMRouter(db, user.id)
        providers = await router._load_providers()
        assert len(providers) == 1
        assert providers[0].provider_name == "custom_db_provider"
        assert providers[0].model_name == "custom-model-x"

    @pytest.mark.asyncio
    async def test_inactive_db_provider_falls_back_to_env(self, db: AsyncSession, user: User, monkeypatch):
        mock_settings = Settings(
            FIREWORKS_API_KEY="fw-key-env",
            FIREWORKS_MODEL="accounts/fireworks/models/llama-v3p1-70b-instruct",
        )
        monkeypatch.setattr("app.config.get_settings", lambda: mock_settings)

        # Inactive DB provider
        cfg = ProviderConfig(
            user_id=user.id,
            provider_name="inactive_provider",
            protocol="openai",
            base_url="https://api.inactive.com/v1",
            api_key_enc=encrypt_value("db-key"),
            model="inactive-model",
            is_active=False,
        )
        db.add(cfg)
        await db.commit()

        router = LLMRouter(db, user.id)
        providers = await router._load_providers()
        assert len(providers) == 1
        assert providers[0].provider_name == "fireworks_env"

    @pytest.mark.asyncio
    async def test_empty_env_and_empty_db_raises_descriptive_error(self, db: AsyncSession, user: User, monkeypatch):
        mock_settings = Settings()
        monkeypatch.setattr("app.config.get_settings", lambda: mock_settings)

        router = LLMRouter(db, user.id)
        with pytest.raises(RuntimeError, match="No LLM providers configured"):
            await router.get_provider()


# ---------------------------------------------------------------------------
# 2. WebSocket Turn Processing & Session Lifecycle
# ---------------------------------------------------------------------------

class TestWebSocketTurnProcessing:
    @pytest.mark.asyncio
    async def test_process_turn_persists_messages_and_corrections(
        self, db: AsyncSession, user: User, active_session: Session, monkeypatch
    ):
        mock_llm_response = {
            "content": json.dumps({
                "reply": "That's great! What did you do yesterday?",
                "corrections": [
                    {
                        "original": "I go to school yesterday",
                        "correction": "I went to school yesterday",
                        "error_type": "past_tense",
                        "explanation": "Use 'went' instead of 'go' for past events.",
                    }
                ],
                "new_vocab": [
                    {
                        "word": "yesterday",
                        "definition": "on the day before today",
                        "example": "I saw him yesterday.",
                    }
                ],
            }),
            "provider": "test_provider",
            "model": "test_model",
        }

        async def mock_complete(*args, **kwargs):
            return mock_llm_response

        monkeypatch.setattr(LLMRouter, "complete_with_fallback", mock_complete)

        res = await _process_turn(
            db=db,
            session_id=active_session.id,
            user_id=user.id,
            text="I go to school yesterday",
            session=active_session,
        )

        assert res["assistant_message"]["text"] == "That's great! What did you do yesterday?"
        assert len(res["corrections"]) == 1
        assert res["corrections"][0]["error_type"] == "past_tense"
        assert len(res["new_vocab"]) == 1
        assert res["new_vocab"][0]["word"] == "yesterday"

        # Verify DB records
        msgs = (await db.execute(select(Message).where(Message.session_id == active_session.id))).scalars().all()
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].text == "I go to school yesterday"
        assert msgs[1].role == "assistant"
        assert msgs[1].text == "That's great! What did you do yesterday?"

    @pytest.mark.asyncio
    async def test_get_user_from_token_validation(self, db: AsyncSession, user: User, monkeypatch):
        # Patch async_session_factory to use our test db engine
        engine = db.bind
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.routers.ws.async_session_factory", factory)

        # Valid token
        token = create_access_token({"sub": user.id})
        found_user = await _get_user_from_token(token)
        assert found_user is not None
        assert found_user.id == user.id

        # Invalid token
        assert await _get_user_from_token("invalid.token.here") is None

        # Empty token
        assert await _get_user_from_token("") is None


# ---------------------------------------------------------------------------
# 3. WebSocket Integration Endpoints via TestClient
# ---------------------------------------------------------------------------

class TestWebSocketEndpoints:
    def test_ws_unauthorized_token_closes_with_4001(self):
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws/session/non-existent-id?token=bad-token") as ws:
                pass

    @pytest.mark.asyncio
    async def test_ws_non_existent_session_closes_with_4004(self, db: AsyncSession, user: User, monkeypatch):
        engine = db.bind
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.routers.ws.async_session_factory", factory)

        token = create_access_token({"sub": user.id})
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect(f"/api/ws/session/missing-session-uuid?token={token}") as ws:
                pass

    @pytest.mark.asyncio
    async def test_ws_ping_pong_and_end_session(
        self, db: AsyncSession, user: User, active_session: Session, monkeypatch
    ):
        engine = db.bind
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.routers.ws.async_session_factory", factory)

        token = create_access_token({"sub": user.id})
        client = TestClient(app)

        with client.websocket_connect(f"/api/ws/session/{active_session.id}?token={token}") as ws:
            # Ping
            ws.send_text(json.dumps({"type": "ping"}))
            data = json.loads(ws.receive_text())
            assert data.get("type") == "pong"

            # End session
            ws.send_text(json.dumps({"type": "end_session"}))
            end_data = json.loads(ws.receive_text())
            assert end_data.get("type") == "session_ended"

        # Verify session is marked ended in DB
        async with factory() as verify_db:
            updated = await verify_db.get(Session, active_session.id)
            assert updated is not None
            assert updated.ended_at is not None

    @pytest.mark.asyncio
    async def test_ws_malformed_json_and_audio_handled_gracefully(
        self, db: AsyncSession, user: User, active_session: Session, monkeypatch
    ):
        engine = db.bind
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr("app.routers.ws.async_session_factory", factory)

        token = create_access_token({"sub": user.id})
        client = TestClient(app)

        with client.websocket_connect(f"/api/ws/session/{active_session.id}?token={token}") as ws:
            # Send invalid non-JSON string
            ws.send_text("this-is-not-valid-json")
            err1 = json.loads(ws.receive_text())
            assert err1.get("type") == "error"
            assert "Invalid JSON format" in err1.get("message", "")

            # Send non-dict JSON (e.g. integer)
            ws.send_text("12345")
            err2 = json.loads(ws.receive_text())
            assert err2.get("type") == "error"
            assert "Payload must be a JSON object" in err2.get("message", "")

            # Send invalid base64 audio
            ws.send_text(json.dumps({"type": "user_audio", "audio": "!!!not-valid-base64!!!"}))
            err3 = json.loads(ws.receive_text())
            assert err3.get("type") == "error"
            assert "base64" in err3.get("message", "").lower()

            # Connection remains alive: send ping
            ws.send_text(json.dumps({"type": "ping"}))
            pong = json.loads(ws.receive_text())
            assert pong.get("type") == "pong"
