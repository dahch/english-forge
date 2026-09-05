from __future__ import annotations

from typing import Optional

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
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    protocol: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    task_routing: Optional[str] = None


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
    default_cefr: Optional[str] = None
    task_routing: Optional[TaskRoutingConfig] = None
