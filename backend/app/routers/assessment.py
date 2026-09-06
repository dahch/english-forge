from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.llm.prompts import ASSESSMENT_ANALYSIS_PROMPT, ASSESSMENT_SYSTEM_PROMPT, build_tutor_persona
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import Assessment, AssessmentMessage, Setting, User
from app.utils import get_tutor_profile_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

MAX_ASSESSMENT_EXCHANGES = 10

_CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}

# End requests are only honored in short, standalone utterances ("I'm done",
# "let's finish"). A bare substring match would force-finish the session when
# the student merely mentions these words ("I'm done with work for today").
_END_WORDS_RE = re.compile(r"\b(finish|end|stop|terminar|basta|done)\b", re.IGNORECASE)


def _wants_to_finish(text: str) -> bool:
    stripped = text.strip().strip(".!?,;:¡¿")
    return len(stripped.split()) <= 4 and bool(_END_WORDS_RE.search(stripped))


def _coerce_json_list(value) -> str:
    """Serialize a value as a JSON list, unwrapping double-encoded strings.

    The LLM sometimes returns a JSON-encoded string where a list is expected;
    storing that raw string would double-encode on the second json.dumps and
    render as [] in the response. Returns '[]' for anything unusable.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    if not isinstance(value, list):
        value = [value] if value else []
    return json.dumps([str(item) for item in value])


class AssessmentMessageCreate(BaseModel):
    # max_length keeps a single turn from writing unbounded text to the DB.
    text: str = Field(..., min_length=1, max_length=2000)


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
    # Transient signal: the conversation phase is over and the client should
    # call /complete. Defaults to False for endpoints that don't compute it.
    is_complete: bool = False

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


# Serializable responses touch the `messages` relationship — it must be
# eagerly loaded, otherwise Pydantic triggers a lazy load outside the
# greenlet context (MissingGreenlet) during response validation.
async def _get_assessment_with_messages(db: AsyncSession, assessment_id: str) -> Assessment:
    result = await db.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(selectinload(Assessment.messages))
    )
    return result.scalar_one()


@router.get("/current", response_model=AssessmentResponse)
async def get_current_assessment(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Prefer an in-progress assessment; if none exists, return the most recent
    # completed one so the client can display results / allow a new attempt.
    result = await db.execute(
        select(Assessment)
        .where(Assessment.user_id == current_user.id)
        .order_by(Assessment.completed_at.is_(None).desc(), Assessment.started_at.desc())
        .options(selectinload(Assessment.messages))
        .limit(1)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="No assessment found")
    return assessment


@router.post("/start", response_model=AssessmentResponse, status_code=201)
async def start_assessment(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Return the in-progress assessment instead of creating a duplicate
    # (double-click on "Start Assessment" would otherwise fork the chat).
    # An idempotent hit is 200, not 201 — nothing was created.
    existing_result = await db.execute(
        select(Assessment)
        .where(Assessment.user_id == current_user.id, Assessment.completed_at.is_(None))
        .order_by(Assessment.started_at.desc())
        .options(selectinload(Assessment.messages))
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        response.status_code = 200
        return existing

    # The partial unique index (uq_assessments_user_in_progress, created at
    # startup) makes this insert atomic: a double-click that passes the
    # SELECT above still can't create a second in-progress row — the loser
    # gets rolled back and re-selects the winner's row.
    assessment = Assessment(user_id=current_user.id)
    try:
        async with db.begin_nested():
            db.add(assessment)
    except IntegrityError:
        existing_result = await db.execute(
            select(Assessment)
            .where(Assessment.user_id == current_user.id, Assessment.completed_at.is_(None))
            .order_by(Assessment.started_at.desc())
            .options(selectinload(Assessment.messages))
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            response.status_code = 200
            return existing
        raise
    await db.refresh(assessment)

    tutor_profile = await get_tutor_profile_dict(db, current_user.id) or {"name": "Sarah", "personality": "friendly"}
    persona = build_tutor_persona(tutor_profile)

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

    return await _get_assessment_with_messages(db, assessment.id)


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

    # Load previous messages — id as a tiebreaker because created_at uses
    # func.now() (the transaction timestamp), so the user message and the
    # assistant reply inserted in the same request can share a timestamp.
    msg_result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    messages = msg_result.scalars().all()

    messages_for_llm = [{"role": m.role, "content": m.text} for m in messages]

    # Assistant messages map 1:1 to questions asked — the greeting message
    # embeds question 1 (see ASSESSMENT_SYSTEM_PROMPT rules 1-2), so the
    # count below is exact and the 10-question cap is enforced server-side.
    questions_asked = sum(1 for m in messages if m.role == "assistant")

    # Server-side completion triggers: the 10-question cap, or the student
    # explicitly asking to finish. Both mean there is nothing left to ask, so
    # short-circuit before the LLM round-trip and go straight to the closing.
    server_complete = questions_asked >= MAX_ASSESSMENT_EXCHANGES or _wants_to_finish(body.text)

    parsed = None
    if not server_complete:
        tutor_profile = await get_tutor_profile_dict(db, current_user.id) or {"name": "Sarah", "personality": "friendly"}
        persona = build_tutor_persona(tutor_profile)

        prompt = f"{persona}\n\n{ASSESSMENT_SYSTEM_PROMPT}\n\nYou have asked {questions_asked} questions so far. The maximum is {MAX_ASSESSMENT_EXCHANGES}. Return is_complete=true if you have reached the maximum or if the student wants to finish."

        llm = LLMRouter(db, current_user.id)
        for attempt in range(3):
            try:
                result = await llm.complete_with_fallback(
                    messages=messages_for_llm,
                    system_prompt=prompt,
                    task="assessment",
                    temperature=0.7,
                    max_tokens=500,
                )
                candidate = parse_llm_json(result["content"])
                if candidate.get("reply", "").strip():
                    parsed = candidate
                    break
                logger.warning(f"Assessment LLM returned empty reply (attempt {attempt + 1}/3)")
            except Exception as e:
                logger.error(f"Assessment message attempt {attempt + 1} failed: {e}")
    else:
        parsed = {
            "reply": "Thank you so much for the chat! Let's see your results now.",
            "question_count": questions_asked,
            "is_complete": True,
        }

    if not parsed:
        # LLM failed and this isn't a server-forced stop — can't continue the
        # conversation, so fall back to the closing message.
        parsed = {"reply": "Thank you! That was a great chat. We'll look at your results now.", "question_count": questions_asked, "is_complete": True}

    # When the model wraps up on its own it is instructed to close with the
    # "results now" line — keep its actual farewell instead of overwriting it.
    # Only server-forced stops (and the fallback above) guarantee that text.
    is_complete = server_complete or bool(parsed.get("is_complete"))

    assistant_msg = AssessmentMessage(
        assessment_id=assessment.id,
        role="assistant",
        text=parsed.get("reply", ""),
    )
    db.add(assistant_msg)
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    response = AssessmentResponse.model_validate(assessment)
    response.is_complete = is_complete
    return response


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
        # Idempotent re-complete: return the stored result. Must re-select with
        # eager loading — serializing the bare ORM object would lazy-load
        # `messages` outside the greenlet (MissingGreenlet → 500).
        return await _get_assessment_with_messages(db, assessment.id)

    msg_result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    messages = msg_result.scalars().all()

    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="Assessment has too few messages to evaluate")

    conversation_text = "\n".join(f"{m.role.upper()}: {m.text}" for m in messages)

    llm = LLMRouter(db, current_user.id)
    raw_analysis = ""
    analysis_result = None
    # Retry up to 3 times when the model returns empty content — smaller/flash
    # models occasionally produce blank responses for structured prompts.
    for attempt in range(3):
        try:
            analysis_result = await llm.complete_with_fallback(
                messages=[{"role": "user", "content": f"{ASSESSMENT_ANALYSIS_PROMPT}\n\nCONVERSATION:\n{conversation_text}"}],
                system_prompt="You are a CEFR assessor. Analyze the conversation and return valid JSON only.",
                task="assessment",
                temperature=0.3,
                max_tokens=1200,
            )
        except Exception as e:
            logger.error(f"Assessment analysis LLM call attempt {attempt + 1}/3 failed for assessment {assessment.id}: {e}")
            if attempt < 2:
                continue
            raise HTTPException(status_code=503, detail="Failed to analyze the assessment. Please try again.")

        raw_analysis = analysis_result.get("content", "")
        logger.info(
            f"Assessment analysis raw response for assessment {assessment.id} "
            f"(provider={analysis_result.get('provider', 'unknown')}, model={analysis_result.get('model', 'unknown')}, "
            f"attempt={attempt + 1}/3, length={len(raw_analysis)}): {raw_analysis[:1500]}"
        )
        if raw_analysis.strip():
            break
        logger.warning(f"Assessment analysis LLM returned empty content (attempt {attempt + 1}/3)")

    if not raw_analysis.strip():
        raise HTTPException(status_code=503, detail="Failed to analyze the assessment. Please try again.")

    try:
        parsed = parse_llm_json(raw_analysis)
    except Exception as e:
        logger.error(f"Assessment analysis JSON parsing failed for assessment {assessment.id}: {e}")
        raise HTTPException(status_code=503, detail="Failed to analyze the assessment. Please try again.")

    logger.info(
        f"Assessment analysis parsed for assessment {assessment.id}: "
        f"keys={list(parsed.keys())}, estimated_level={parsed.get('estimated_level')}, "
        f"confidence={parsed.get('confidence')}"
    )

    assessment.completed_at = datetime.now(timezone.utc)
    raw_level = str(parsed.get("estimated_level") or "").strip().upper()
    if raw_level not in _CEFR_LEVELS:
        # LLM returned null/garbage — fall back to the user's preferred CEFR
        # level (from settings) so we don't demote them or crash on the NOT
        # NULL VARCHAR(2) column.
        cefr_setting = await db.execute(
            select(Setting).where(Setting.user_id == current_user.id, Setting.key == "default_cefr")
        )
        preferred = cefr_setting.scalar_one_or_none()
        preferred_level = (preferred.value or "").strip().upper() if preferred else ""
        raw_level = (
            preferred_level
            if preferred_level in _CEFR_LEVELS
            else current_user.current_level
            if current_user.current_level in _CEFR_LEVELS
            else "A1"
        )
        logger.warning(
            f"Assessment {assessment.id} returned invalid level '{parsed.get('estimated_level')}', "
            f"falling back to {raw_level}"
        )
    assessment.estimated_level = raw_level
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    assessment.confidence = min(1.0, max(0.0, confidence))
    # Coerce double-encoded strings back to lists — the LLM occasionally
    # wraps an array in a JSON string, which would otherwise double-encode
    # and render as [] in the response.
    assessment.strengths = _coerce_json_list(parsed.get("strengths"))
    assessment.weaknesses = _coerce_json_list(parsed.get("weaknesses"))
    assessment.recommendations = _coerce_json_list(parsed.get("recommendations"))
    assessment.summary = parsed.get("summary", "")

    # Update user level and mark assessment completed
    current_user.current_level = assessment.estimated_level
    current_user.assessment_completed = True

    await db.flush()

    logger.info(
        f"Assessment {assessment.id} completed for user {current_user.id}: "
        f"level={assessment.estimated_level}, confidence={assessment.confidence}, "
        f"strengths={len(json.loads(assessment.strengths or '[]'))}, "
        f"weaknesses={len(json.loads(assessment.weaknesses or '[]'))}"
    )

    return await _get_assessment_with_messages(db, assessment.id)
