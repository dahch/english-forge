from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ProviderConfigCreate(BaseModel):
    provider_name: str = Field(..., max_length=100)
    api_key: str = Field(..., min_length=1)
    base_url: str = Field(..., max_length=500)
    model: str = Field(..., max_length=200)
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    is_active: bool = True
    priority: int = 0
    task_routing: str = Field(default="conversation,correction,lesson")


class ProviderConfigUpdate(BaseModel):
    provider_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    protocol: Optional[str] = Field(default=None, pattern="^(openai|anthropic)$")
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    task_routing: Optional[str] = None


class ProviderTestRequest(BaseModel):
    """Probe an unsaved provider configuration from the Settings form."""

    provider_name: str = Field(default="test", max_length=100)
    protocol: str = Field(default="openai", pattern="^(openai|anthropic)$")
    base_url: str = Field(..., max_length=500)
    model: str = Field(..., min_length=1, max_length=200)
    api_key: str = Field(..., min_length=1, max_length=500)


class ProviderTestResponse(BaseModel):
    ok: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    reply: Optional[str] = None
    error: Optional[str] = None


class ProviderConfigResponse(BaseModel):
    id: str
    provider_name: str
    base_url: str
    model: str
    protocol: str
    is_active: bool
    priority: int
    task_routing: str
    has_api_key: bool = True

    model_config = {"from_attributes": True}


class TaskRoutingConfig(BaseModel):
    conversation: Optional[str] = None
    correction: Optional[str] = None
    lesson: Optional[str] = None


class SettingResponse(BaseModel):
    key: str
    value: str

    model_config = {"from_attributes": True}


class UserSettingsUpdate(BaseModel):
    stt_mode: Optional[str] = None
    tts_voice: Optional[str] = None
    default_cefr: Optional[Literal["A1", "A2", "B1", "B2", "C1", "C2"]] = None
    task_routing: Optional[TaskRoutingConfig] = None
    regenerate_path: Optional[bool] = False
