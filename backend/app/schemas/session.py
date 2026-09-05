from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from pydantic import BaseModel, Field


class CorrectionSchema(BaseModel):
    error_type: str
    original: str
    correction: str
    explanation: str = ""


class VocabSchema(BaseModel):
    word: str
    definition: str = ""
    example: str = ""


class LLMResponseSchema(BaseModel):
    reply: str
    corrections: list[CorrectionSchema] = Field(default_factory=list)
    new_vocab: list[VocabSchema] = Field(default_factory=list)


class ScenarioCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: str = Field(default="", max_length=1000)
    cefr_level: str = Field(default="B1", pattern="^[A-C][1-2]$")


class ScenarioResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    cefr_level: str
    is_custom: bool

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    scenario_id: Optional[str] = None
    cefr_level: str = Field(default="B1", pattern="^[A-C][1-2]$")


class SessionResponse(BaseModel):
    id: str
    scenario_id: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    cefr_level: str
    provider_used: Optional[str] = None
    scenario_name: Optional[str] = None
    message_count: int = 0

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    text: str = Field(..., min_length=1)


class CorrectionResponse(BaseModel):
    id: str
    error_type: str
    original_fragment: str
    correction: str
    explanation: str

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    text: str
    audio_url: Optional[str] = None
    created_at: datetime
    corrections: list[CorrectionResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ConversationTurnResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
    corrections: list[CorrectionResponse]
    new_vocab: list[dict]
    audio_url: Optional[str] = None


class SessionSummaryResponse(BaseModel):
    session_id: str
    duration_minutes: float
    total_messages: int
    corrections_by_type: dict[str, int]
    total_corrections: int
    new_words_learned: int
    corrections_list: list[CorrectionResponse]
    new_vocab_list: list[dict]
