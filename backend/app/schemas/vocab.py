from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from pydantic import BaseModel, Field


class VocabItemCreate(BaseModel):
    word: str = Field(..., max_length=200)
    definition: str = Field(default="", max_length=1000)
    example: str = Field(default="", max_length=500)
    ipa: Optional[str] = Field(default=None, max_length=200)


class VocabItemUpdate(BaseModel):
    word: Optional[str] = None
    definition: Optional[str] = None
    example: Optional[str] = None
    ipa: Optional[str] = None


class VocabItemResponse(BaseModel):
    id: str
    word: str
    definition: str
    example: str
    ipa: Optional[str] = None
    ease_factor: float
    interval_days: int
    next_review_at: Optional[date] = None
    last_reviewed_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewSubmission(BaseModel):
    quality: int = Field(..., ge=0, le=5)


class ReviewCardResponse(BaseModel):
    id: str
    word: str
    definition: str
    example: str
    ipa: Optional[str] = None
    ease_factor: float
    interval_days: int

    model_config = {"from_attributes": True}


class QuizQuestion(BaseModel):
    vocab_item_id: str
    question_type: str
    question: str
    options: list[str] = Field(default_factory=list)
    correct_answer: str


class QuizSubmission(BaseModel):
    vocab_item_id: str
    answer: str


class QuizResult(BaseModel):
    correct: bool
    correct_answer: str
    explanation: str = ""
