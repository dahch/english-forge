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

    JWT_SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_MINUTES: int = 1440

    SETTINGS_ENCRYPTION_KEY: str = ""

    APP_PIN: Optional[str] = None

    PERSONAL_API_URL: str = "http://personal-api:8000"
    TTS_DEFAULT_VOICE: str = "alba"
    TTS_JOB_POLL_INTERVAL_MS: int = 500
    TTS_JOB_TIMEOUT_SECONDS: int = 30

    # Per-request timeout for LLM providers. Reasoning models can take a while
    # to "think" before producing output — keep this generous or unset
    # (None disables the timeout entirely).
    LLM_TIMEOUT_SECONDS: Optional[int] = 300

    STT_MODE: str = "web_speech"
    WHISPER_MODEL: str = "small"

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
