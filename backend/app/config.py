from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "EnglishForge"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8230

    DATABASE_URL: str = "sqlite+aiosqlite:///./data/englishforge.db"

    # No default on purpose: a missing/weak secret must stop the process at
    # startup (see app.security.validate_security_settings) instead of letting
    # the app run with a publicly known signing key.
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    # Access tokens are short-lived and renewed transparently by the refresh
    # token cookie (see ADR-022). A long-lived access token in localStorage was
    # the previous design.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    # Cap concurrent devices; the oldest refresh tokens are revoked beyond this.
    MAX_REFRESH_TOKENS_PER_USER: int = 10

    # --- Session cookies ----------------------------------------------------
    # Set COOKIE_SECURE=true on any HTTPS deployment. It defaults to false so
    # plain-HTTP local development still receives the cookie.
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: str = ""

    # Escape hatch for CI/local experiments only. Never set this in production:
    # it downgrades the startup validation to a warning.
    ALLOW_INSECURE_DEFAULTS: bool = False

    # Dedicated Fernet key for provider API keys. Empty = derived from
    # JWT_SECRET_KEY (legacy behaviour, logs a warning). A dedicated key keeps
    # encrypted secrets decryptable when the JWT secret is rotated.
    SETTINGS_ENCRYPTION_KEY: str = ""

    APP_PIN: Optional[str] = None

    # --- Auth hardening -----------------------------------------------------
    # Failed logins are counted per email and per client IP; 429 + Retry-After
    # once the window budget is exhausted.
    AUTH_RATE_LIMIT_ATTEMPTS: int = 5
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 900
    # Registrations are counted per client IP (successes included).
    REGISTER_RATE_LIMIT_ATTEMPTS: int = 5
    REGISTER_RATE_LIMIT_WINDOW_SECONDS: int = 3600
    # The backend usually sits behind the Next.js rewrite proxy, so the client
    # IP arrives in X-Forwarded-For. Disable when exposed directly.
    TRUST_PROXY_HEADERS: bool = True

    # --- Data lifecycle -----------------------------------------------------
    # An in-progress assessment with no activity for this long is abandoned
    # automatically so the user is never locked out of starting a new one.
    ASSESSMENT_INACTIVE_DAYS: int = 7
    # Wall-clock cap for a session's billable minutes (an idle tab left open
    # overnight must not inflate ProgressDaily).
    MAX_SESSION_MINUTES: int = 180
    # Grace period after the last message before the session is considered
    # finished, for the duration calculation.
    SESSION_END_GRACE_MINUTES: int = 2

    PERSONAL_API_URL: str = "http://personal-api:8000"
    TTS_DEFAULT_VOICE: str = "alba"
    # Pocket TTS model id sent on every /v1/speak call. The personal-api
    # server defaults to a Spanish model; english-forge always pins the
    # English one. Unknown ids are ignored by Pocket TTS (hot-swap safe).
    TTS_MODEL: str = "english_2026-04_24l"
    TTS_JOB_POLL_INTERVAL_MS: int = 500
    TTS_JOB_TIMEOUT_SECONDS: int = 30

    # Per-request timeout for LLM providers. Reasoning models can take a while
    # to "think" before producing output — keep this generous or unset
    # (None disables the timeout entirely).
    LLM_TIMEOUT_SECONDS: Optional[int] = 300

    STT_MODE: str = "web_speech"
    WHISPER_MODEL: str = "small"
    # Language hint sent on every personal-api /v1/transcribe call. The app is
    # an English tutor, so "en" — which is also what unlocks real Moonshine
    # word timestamps (the basis of the pronunciation fluency metrics).
    STT_LANGUAGE: str = "en"

    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4.1-mini"

    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    FIREWORKS_API_KEY: Optional[str] = None
    FIREWORKS_BASE_URL: str = "https://api.fireworks.ai/inference/v1"
    FIREWORKS_MODEL: str = "accounts/fireworks/models/llama-v3p1-70b-instruct"

    CLINEPASS_API_KEY: Optional[str] = None
    CLINEPASS_BASE_URL: Optional[str] = None
    CLINEPASS_MODEL: Optional[str] = None

    CUSTOM_API_KEY: Optional[str] = None
    CUSTOM_BASE_URL: Optional[str] = None
    CUSTOM_MODEL: Optional[str] = None

    CORS_ORIGINS: str = "http://localhost:3590,http://127.0.0.1:3590"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
