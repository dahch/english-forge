from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import Assessment, AssessmentMessage, User
from app.utils import get_tutor_profile_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

MAX_ASSESSMENT_EXCHANGES = 10

# Word-boundary match so "weekend"/"friend" don't trigger an early finish.
_END_REQUEST_RE = re.compile(r"\b(finish|end|stop|terminar|done)\b", re.IGNORECASE)


ASSESSMENT_SYSTEM_PROMPT = """You are an expert English teacher and certified CEFR assessor. You are conducting a friendly placement conversation to gauge the student's English proficiency.

Your persona:
- Warm, encouraging, and professional.
- You speak ONLY in English during the conversation.
- You ask natural, conversational questions — not textbook test questions.
- You subtly adapt difficulty based on the student's replies: if their grammar and vocabulary are strong, ask more abstract or nuanced questions; if they struggle, simplify and ask concrete questions.

Rules for the conversation:
1. Greet the student warmly and ask the first simple question (e.g., about their name, where they are from, or what they do).
2. Ask up to 10 questions in total. Count only your own questions (not the greeting).
3. Each question should be slightly more complex than the previous one when the student answers well.
4. If the student makes many errors, ask easier, more concrete questions to keep them comfortable.
5. Keep each reply concise (1-3 sentences). The goal is to hear the student speak, not to lecture.
6. Do NOT explicitly say this is a test or exam. Frame it as a friendly chat.
7. If the student says "finish", "end", "stop", or "terminar", stop asking questions and say something like "Thank you, that was great! We'll look at your results now."
8. After 10 questions, say "Thank you, that was great! We'll look at your results now." and do not ask more questions.

You must respond in valid JSON with this structure:
{
  "reply": "Your conversational response to the student, including the next question or the closing message.",
  "question_count": number,
  "is_complete": boolean
}
"""


ASSESSMENT_ANALYSIS_PROMPT = """You are an expert English teacher and CEFR assessor. Analyze the following conversation between a student and an English tutor. The tutor asked natural questions to gauge the student's proficiency.

Evaluate the student across these dimensions:
- Grammar accuracy and range (verb tenses, sentence structures, articles, prepositions)
- Vocabulary range and precision (word choice, collocations, idiomatic expressions)
- Fluency and coherence (length and flow of responses, use of connectors)
- Listening/reading comprehension (do the answers address the questions appropriately?)
- Pronunciation proxy (based on spelling and word choice, since we only have text)

Based on the CEFR levels (A1, A2, B1, B2, C1, C2), assign an estimated level. Be conservative: only assign a higher level if the student consistently demonstrates the required abilities. If the conversation is very short, lower your confidence.

Return a JSON object exactly like this:
{
  "estimated_level": "A1|A2|B1|B2|C1|C2",
  "confidence": 0.0-1.0,
  "strengths": ["grammar", "vocabulary", "fluency", "listening", "pronunciation"],
  "weaknesses": ["grammar", "vocabulary", "fluency", "listening", "pronunciation"],
  "recommendations": ["specific recommendation 1", "specific recommendation 2", "specific recommendation 3"],
  "summary": "A brief paragraph in Spanish explaining the student's level and what they can do now."
}

Strengths, weaknesses, and recommendations should be concrete and actionable. Write the summary in Spanish."""


class AssessmentMessageCreate(BaseModel):
    text: str = Field(..., min_length=1)


class AssessmentMessageResponse(BaseModel):
    id: str
    role: str
    text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AssessmentResponse(BaseModel):
    id: str
    started_at: datetime
    completed_at: datetime | None
    estimated_level: str | None
    confidence: float | None
    strengths: list[str] | None
    weaknesses: list[str] | None
    recommendations: list[str] | None
    summary: str | None
    messages: list[AssessmentMessageResponse]

    model_config = {"from_attributes": True}

    @field_validator("strengths", "weaknesses", "recommendations", mode="before")
    @classmethod
    def parse_json_lists(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v


class AssessmentResult(BaseModel):
    estimated_level: str
    confidence: float
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    summary: str


@router.get("/current", response_model=AssessmentResponse)
async def get_current_assessment(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Assessment)
        .where(Assessment.user_id == current_user.id, Assessment.completed_at.is_(None))
        .order_by(Assessment.started_at.desc())
        .limit(1)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="No in-progress assessment")
    return assessment


@router.post("/start", response_model=AssessmentResponse, status_code=201)
async def start_assessment(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Return the in-progress assessment instead of creating a duplicate
    # (double-click on "Start Assessment" would otherwise fork the chat).
    existing_result = await db.execute(
        select(Assessment)
        .where(Assessment.user_id == current_user.id, Assessment.completed_at.is_(None))
        .order_by(Assessment.started_at.desc())
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return existing

    assessment = Assessment(user_id=current_user.id)
    db.add(assessment)
    await db.flush()
    await db.refresh(assessment)

    tutor_profile = await get_tutor_profile_dict(db, current_user.id) or {"name": "Sarah", "personality": "friendly"}
    persona = f"You are {tutor_profile['name']}, an expert English teacher."

    prompt = f"{persona}\n\n{ASSESSMENT_SYSTEM_PROMPT}\n\nThis is the start of the assessment. Greet the student and ask your first question. Return question_count=1 and is_complete=false."

    llm = LLMRouter(db, current_user.id)
    try:
        result = await llm.complete_with_fallback(
            messages=[],
            system_prompt=prompt,
            task="assessment",
            temperature=0.7,
            max_tokens=500,
        )
        parsed = parse_llm_json(result["content"])
    except Exception as e:
        logger.error(f"Assessment start failed: {e}")
        parsed = {"reply": "Hi! I'm your English tutor. Let's start with a simple question: what do you like to do in your free time?", "question_count": 1, "is_complete": False}

    assistant_msg = AssessmentMessage(
        assessment_id=assessment.id,
        role="assistant",
        text=parsed.get("reply", ""),
    )
    db.add(assistant_msg)
    await db.flush()

    await db.refresh(assessment)
    return assessment


@router.post("/{assessment_id}/message", response_model=AssessmentResponse)
async def assessment_message(
    assessment_id: str,
    body: AssessmentMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id, Assessment.user_id == current_user.id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.completed_at:
        raise HTTPException(status_code=400, detail="Assessment already completed")

    user_msg = AssessmentMessage(assessment_id=assessment.id, role="user", text=body.text)
    db.add(user_msg)
    await db.flush()

    # Load previous messages
    msg_result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at)
    )
    messages = msg_result.scalars().all()

    messages_for_llm = [{"role": m.role, "content": m.text} for m in messages]

    # Count assistant questions so far
    assistant_count = sum(1 for m in messages if m.role == "assistant")
    is_complete = assistant_count >= MAX_ASSESSMENT_EXCHANGES

    # Check if user wants to finish early
    if _END_REQUEST_RE.search(body.text):
        is_complete = True

    tutor_profile = await get_tutor_profile_dict(db, current_user.id) or {"name": "Sarah", "personality": "friendly"}
    persona = f"You are {tutor_profile['name']}, an expert English teacher."

    prompt = f"{persona}\n\n{ASSESSMENT_SYSTEM_PROMPT}\n\nYou have asked {assistant_count} questions so far. The maximum is {MAX_ASSESSMENT_EXCHANGES}. Return is_complete=true if you have reached the maximum or if the student wants to finish."

    llm = LLMRouter(db, current_user.id)
    try:
        result = await llm.complete_with_fallback(
            messages=messages_for_llm,
            system_prompt=prompt,
            task="assessment",
            temperature=0.7,
            max_tokens=500,
        )
        parsed = parse_llm_json(result["content"])
    except Exception as e:
        logger.error(f"Assessment message failed: {e}")
        is_complete = True
        parsed = {"reply": "Thank you! That was a great chat. We'll look at your results now.", "question_count": assistant_count, "is_complete": True}

    if parsed.get("is_complete") or is_complete:
        parsed["is_complete"] = True
        parsed["reply"] = "Thank you so much for the chat! Let's see your results now."

    assistant_msg = AssessmentMessage(
        assessment_id=assessment.id,
        role="assistant",
        text=parsed.get("reply", ""),
    )
    db.add(assistant_msg)
    await db.flush()

    await db.refresh(assessment)
    return assessment


@router.post("/{assessment_id}/complete", response_model=AssessmentResponse)
async def complete_assessment(
    assessment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id, Assessment.user_id == current_user.id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if assessment.completed_at:
        return assessment

    msg_result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at)
    )
    messages = msg_result.scalars().all()

    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="Assessment has too few messages to evaluate")

    conversation_text = "\n".join(f"{m.role.upper()}: {m.text}" for m in messages)

    llm = LLMRouter(db, current_user.id)
    try:
        analysis_result = await llm.complete_with_fallback(
            messages=[{"role": "user", "content": f"{ASSESSMENT_ANALYSIS_PROMPT}\n\nCONVERSATION:\n{conversation_text}"}],
            system_prompt="You are a CEFR assessor. Analyze the conversation and return valid JSON only.",
            task="assessment",
            temperature=0.3,
            max_tokens=1200,
        )
        parsed = parse_llm_json(analysis_result["content"])
    except Exception as e:
        logger.error(f"Assessment analysis failed: {e}")
        raise HTTPException(status_code=503, detail=f"Failed to analyze assessment: {e}")

    assessment.completed_at = datetime.now(timezone.utc)
    assessment.estimated_level = parsed.get("estimated_level", "A1")
    assessment.confidence = parsed.get("confidence", 0.5)
    assessment.strengths = json.dumps(parsed.get("strengths", []))
    assessment.weaknesses = json.dumps(parsed.get("weaknesses", []))
    assessment.recommendations = json.dumps(parsed.get("recommendations", []))
    assessment.summary = parsed.get("summary", "")

    # Update user level and mark assessment completed
    current_user.current_level = assessment.estimated_level
    current_user.assessment_completed = True

    await db.flush()
    await db.refresh(assessment)

    return assessment
