from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


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


class MessageCreate(BaseModel):
    # Same cap as assessment turns: an unbounded message is a cost/DoS vector
    # for the LLM and bloats the history sent on every subsequent turn.
    text: str = Field(..., min_length=1, max_length=2000)


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
    # Kept for wire compatibility but always null: audio lives on disk and is
    # fetched from the authenticated endpoint (never as a base64 blob).
    audio_url: Optional[str] = None
    has_audio: bool = False
    created_at: datetime
    corrections: list[CorrectionResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _hide_audio_reference(self):
        # Idempotent: FastAPI/Pydantic may validate the same instance again
        # when it is nested in a response, and this must not reset the flag.
        self.has_audio = bool(self.has_audio or self.audio_url)
        self.audio_url = None
        return self


class SessionResponse(BaseModel):
    id: str
    scenario_id: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    cefr_level: str
    provider_used: Optional[str] = None
    scenario_name: Optional[str] = None
    message_count: int = 0
    initial_message: Optional[MessageResponse] = None

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
