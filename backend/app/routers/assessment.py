"""Multi-skill placement assessment.

Sections (phases), tracked server-side on Assessment.phase:
  mic_check    — one read-aloud sentence; verifies the mic + STT pipeline.
  conversation — tutor interview by voice/text (grammar, vocabulary, fluency).
                 MIN_ASSESSMENT_EXCHANGES gate before it can end.
  listening    — audio-only comprehension items (pocket-tts playback, hidden
                 text), graded per item.
  speaking     — read-aloud sentences scored deterministically (WER/PER/
                 fluency) via POST /recordings + client-sent metrics.

The LLM never decides the final level or confidence — those come from
deterministic aggregation (app.services.assessment_scoring). The LLM scores
the conversation dimensions with a rubric and writes the qualitative summary.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.prompts import (
    ASSESSMENT_ANALYSIS_PROMPT,
    ASSESSMENT_SYSTEM_PROMPT,
    LISTENING_GRADING_PROMPT,
    build_tutor_persona,
)
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import Assessment, AssessmentMessage, Setting, User
from app.services.assessment_bank import (
    LISTENING_EARLY_STOP_FAILS,
    LISTENING_ITEMS,
    SPEAKING_ITEMS,
    ListeningItem,
    SpeakingItem,
)
from app.services.assessment_scoring import (
    compute_confidence,
    final_level,
    mean_or_none,
    strengths_weaknesses,
)
from app.services.pronunciation import normalize_text, score_pronunciation
from app.services.stt import transcribe_audio
from app.utils import get_tutor_profile_dict, resolve_tts_voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

MAX_ASSESSMENT_EXCHANGES = 10
MIN_ASSESSMENT_EXCHANGES = 6

# Phases (assessment.phase)
PHASE_MIC_CHECK = "mic_check"
PHASE_CONVERSATION = "conversation"
PHASE_LISTENING = "listening"
PHASE_SPEAKING = "speaking"

# Message kinds (assessment_messages.kind)
KIND_CHAT = "chat"
KIND_MIC_CHECK = "mic_check"
KIND_LISTENING = "listening"
KIND_SPEAKING = "speaking"

_MIC_CHECK_TEXT = (
    "Let's make sure I can hear you clearly. "
    "Please read this sentence aloud: The quick brown fox jumps over the lazy dog."
)
_MIC_CHECK_EXPECTED = "The quick brown fox jumps over the lazy dog."

# Maximum accepted recording upload (30s of opus/webm is well under this).
_MAX_RECORDING_BYTES = 10 * 1024 * 1024

_CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}

# Reanalyze cost guard: cooldown between LLM analyses of the same assessment.
_REANALYZE_COOLDOWN = timedelta(seconds=30)
_reanalyze_last: dict[str, datetime] = {}

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


def _clamp_score(value, default: float | None = None) -> float | None:
    """Coerce an LLM-produced dimension score to a clamped float."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, score))


class AssessmentMessageCreate(BaseModel):
    # max_length keeps a single turn from writing unbounded text to the DB.
    text: str = Field(..., min_length=1, max_length=2000)
    # "voice" when the text came from /recordings (STT), "text" when typed.
    source: str = Field("text", pattern="^(text|voice)$")
    # Evidence captured at recording time (pronunciation metrics for speaking
    # items). Only stored, never trusted for conversation answers.
    metrics: dict | None = None


class AssessmentMessageResponse(BaseModel):
    id: str
    role: str
    text: str
    kind: str
    audio_url: str | None
    metrics: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("metrics", mode="before")
    @classmethod
    def parse_metrics(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v


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
    phase: str | None
    dimension_scores: dict | None
    messages: list[AssessmentMessageResponse]
    # Transient signal: every section is done and the client should call
    # /complete. Defaults to False for endpoints that don't compute it.
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

    @field_validator("dimension_scores", mode="before")
    @classmethod
    def parse_dimension_scores(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v


class AssessmentResult(BaseModel):
    estimated_level: str
    confidence: float
    strengths: list[str]
    weaknesses: list[str]
    recommendations: list[str]
    summary: str


class RecordingResponse(BaseModel):
    transcript: str
    words: list[dict]
    # Pronunciation metrics — present when expected_text was supplied.
    metrics: dict | None = None


# Serializable responses touch the `messages` relationship — it must be
# eagerly loaded, otherwise Pydantic triggers a lazy load outside the
# greenlet context (MissingGreenlet) during response validation.
#
# populate_existing forces the messages collection to re-load even when the
# Assessment object is already in the identity map: phase transitions add
# messages earlier in the same request (farewell + next item), and without
# this the response would silently omit them.
async def _get_assessment_with_messages(db: AsyncSession, assessment_id: str) -> Assessment:
    result = await db.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(selectinload(Assessment.messages))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _assistant_message(assessment_id: str, text: str, kind: str) -> AssessmentMessage:
    return AssessmentMessage(assessment_id=assessment_id, role="assistant", text=text, kind=kind)


def _user_message(
    assessment_id: str, text: str, kind: str, source: str, metrics: dict | None
) -> AssessmentMessage:
    msg = AssessmentMessage(
        assessment_id=assessment_id, role="user", text=text, kind=kind
    )
    if source == "voice" or metrics is not None:
        payload = dict(metrics or {})
        payload["source"] = source
        msg.metrics = json.dumps(payload, ensure_ascii=False)
    return msg


def _item_for_step(phase: str, step: int):
    """Current banked item for a phase/step, or None when the bank is done."""
    if phase == PHASE_LISTENING:
        return LISTENING_ITEMS[step] if step < len(LISTENING_ITEMS) else None
    if phase == PHASE_SPEAKING:
        return SPEAKING_ITEMS[step] if step < len(SPEAKING_ITEMS) else None
    return None


def _expected_text_for(phase: str, step: int) -> str | None:
    if phase == PHASE_MIC_CHECK:
        return _MIC_CHECK_EXPECTED
    if phase == PHASE_SPEAKING:
        item = _item_for_step(phase, step)
        return item.text if item else None
    return None


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
    assessment = Assessment(user_id=current_user.id, phase=PHASE_MIC_CHECK)
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
    await db.flush()

    # The first message is a deterministic mic check — no LLM round-trip. It
    # validates permissions/volume/STT before the interview starts.
    db.add(_assistant_message(assessment.id, _MIC_CHECK_TEXT, KIND_MIC_CHECK))
    await db.flush()

    return await _get_assessment_with_messages(db, assessment.id)


async def _synth_assistant_tts(
    db: AsyncSession, current_user: User, assessment: Assessment, msg: AssessmentMessage
) -> bytes | None:
    """Lazily synthesize TTS for an assistant message, caching the data URI
    on the row. Returns the raw bytes, or None when TTS is unavailable."""
    if msg.audio_url and msg.audio_url.startswith("data:"):
        header, _, payload = msg.audio_url.partition(",")
        try:
            return base64.b64decode(payload)
        except Exception:
            pass
    voice = await resolve_tts_voice(db, current_user.id)
    try:
        audio = await TTSPersonalAPI().synthesize(msg.text, voice=voice)
    except Exception as e:
        logger.error(f"Assessment TTS synthesis failed for message {msg.id}: {e}")
        return None
    if audio:
        msg.audio_url = f"data:audio/mpeg;base64,{base64.b64encode(audio).decode()}"
        await db.flush()
    return audio


@router.get("/{assessment_id}/messages/{message_id}/audio")
async def get_message_audio(
    assessment_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Assessment).where(
            Assessment.id == assessment_id, Assessment.user_id == current_user.id
        )
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    msg_result = await db.execute(
        select(AssessmentMessage).where(
            AssessmentMessage.id == message_id,
            AssessmentMessage.assessment_id == assessment_id,
        )
    )
    msg = msg_result.scalar_one_or_none()
    if not msg or msg.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found")

    audio = await _synth_assistant_tts(db, current_user, assessment, msg)
    if not audio:
        raise HTTPException(status_code=503, detail="Audio generation is unavailable right now")
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/{assessment_id}/recordings", response_model=RecordingResponse)
async def upload_recording(
    assessment_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe a student recording (Moonshine via personal-api, in-process
    whisper as fallback) and, when the phase has an expected text, score
    pronunciation deterministically. Recordings are never persisted."""
    result = await db.execute(
        select(Assessment).where(
            Assessment.id == assessment_id,
            Assessment.user_id == current_user.id,
            Assessment.completed_at.is_(None),
        )
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty recording")
    if len(audio) > _MAX_RECORDING_BYTES:
        raise HTTPException(status_code=413, detail="Recording too large (max 10MB)")

    content_type = file.content_type or "audio/webm"
    stt = await transcribe_audio(audio, content_type)

    expected = _expected_text_for(assessment.phase, assessment.section_step)
    metrics = None
    if expected and stt["text"].strip():
        metrics = score_pronunciation(expected, stt["text"], stt["words"])
    return RecordingResponse(transcript=stt["text"], words=stt["words"], metrics=metrics)


async def _grade_listening_answer(
    db: AsyncSession, current_user: User, item: ListeningItem, answer: str
) -> dict:
    """Grade a listening answer with the LLM; deterministic keyword fallback
    when the LLM is unavailable (grading must never break the message flow)."""
    prompt = LISTENING_GRADING_PROMPT.format(
        item_text=item.tutor_text,
        expected_answer=item.expected_answer,
        student_answer=answer,
    )
    llm = LLMRouter(db, current_user.id)
    for attempt in range(2):
        try:
            result = await llm.complete_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You grade English listening comprehension. Return valid JSON only.",
                task="assessment",
                temperature=0.0,
                max_tokens=200,
            )
            parsed = parse_llm_json(result["content"])
            if isinstance(parsed.get("correct"), bool):
                return {
                    "correct": 1 if parsed["correct"] else 0,
                    "reason": str(parsed.get("reason", ""))[:500],
                }
        except Exception as e:
            logger.error(f"Listening grading attempt {attempt + 1}/2 failed: {e}")

    # Keyword fallback: fraction of the expected answer's content words that
    # appear in the student's response.
    stop = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "at", "on", "and"}
    expected_words = {w for w in normalize_text(item.expected_answer) if w not in stop}
    answered_words = set(normalize_text(answer))
    overlap = (len(expected_words & answered_words) / len(expected_words)) if expected_words else 0.0
    return {
        "correct": 1 if overlap >= 0.4 else 0,
        "reason": "Graded by keyword matching (grading model unavailable).",
    }


def _conversation_questions_asked(messages: list[AssessmentMessage]) -> int:
    return sum(1 for m in messages if m.role == "assistant" and m.kind == KIND_CHAT)


async def _build_next_question(
    db: AsyncSession,
    current_user: User,
    assessment: Assessment,
    messages: list[AssessmentMessage],
) -> dict | None:
    """Ask the tutor LLM for the next conversation question.

    Returns the parsed {"reply", "is_complete", ...} dict, or None when every
    attempt failed (callers then continue with a fixed recovery line)."""
    messages_for_llm = [{"role": m.role, "content": m.text} for m in messages]
    questions_asked = _conversation_questions_asked(messages)

    tutor_profile = await get_tutor_profile_dict(db, current_user.id) or {"name": "Sarah", "personality": "friendly"}
    persona = build_tutor_persona(tutor_profile)
    prompt = (
        f"{persona}\n\n{ASSESSMENT_SYSTEM_PROMPT}\n\n"
        f"You have asked {questions_asked} questions so far. "
        f"The maximum is {MAX_ASSESSMENT_EXCHANGES}. "
        f"Only mark is_complete=true if the student explicitly asked to stop."
    )

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
            parsed = parse_llm_json(result["content"])
            if parsed.get("reply", "").strip():
                return parsed
            logger.warning(f"Assessment LLM returned empty reply (attempt {attempt + 1}/3)")
        except Exception as e:
            logger.error(f"Assessment message attempt {attempt + 1} failed: {e}")
    return None


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

    phase = assessment.phase or PHASE_CONVERSATION
    if phase == PHASE_MIC_CHECK:
        return await _handle_mic_check(db, current_user, assessment, body)
    if phase == PHASE_CONVERSATION:
        return await _handle_conversation(db, current_user, assessment, body)
    if phase == PHASE_LISTENING:
        return await _handle_listening(db, current_user, assessment, body)
    if phase == PHASE_SPEAKING:
        return await _handle_speaking(db, current_user, assessment, body)
    raise HTTPException(status_code=400, detail=f"Unknown assessment phase: {phase}")


async def _handle_mic_check(
    db: AsyncSession, current_user: User, assessment: Assessment, body: AssessmentMessageCreate
) -> AssessmentResponse:
    db.add(_user_message(assessment.id, body.text, KIND_MIC_CHECK, body.source, body.metrics))

    # Move to the interview and ask question 1 (LLM greets + asks).
    assessment.phase = PHASE_CONVERSATION
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    parsed = await _build_next_question(db, current_user, assessment, list(assessment.messages))
    reply = parsed.get("reply", "") if parsed else (
        "Great, your microphone works! So, let's start easy: what do you like to do in your free time?"
    )
    db.add(_assistant_message(assessment.id, reply, KIND_CHAT))
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = False
    return resp


async def _handle_conversation(
    db: AsyncSession, current_user: User, assessment: Assessment, body: AssessmentMessageCreate
) -> AssessmentResponse:
    db.add(_user_message(assessment.id, body.text, KIND_CHAT, body.source, body.metrics))
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    messages = list(assessment.messages)
    questions_asked = _conversation_questions_asked(messages)

    # Server-side completion triggers: the 10-question cap, or the student
    # explicitly asking to finish. The LLM can only end the section on its own
    # once MIN_ASSESSMENT_EXCHANGES have happened — a 2-3 answer interview is
    # not enough evidence to place a level.
    server_complete = (
        questions_asked >= MAX_ASSESSMENT_EXCHANGES or _wants_to_finish(body.text)
    )
    parsed = None
    if not server_complete:
        parsed = await _build_next_question(db, current_user, assessment, messages)
        if parsed is None:
            # LLM failed and this isn't a server-forced stop — recover with a
            # fixed line and keep the conversation alive (the student's next
            # message retries the LLM). We never transition phases on an LLM
            # outage: completing sections is evidence-based, not accidental.
            parsed = {
                "reply": "Sorry, I lost my train of thought for a second — tell me a bit more about that.",
                "question_count": questions_asked,
                "is_complete": False,
            }

    llm_complete = bool(parsed and parsed.get("is_complete"))
    if server_complete or (llm_complete and questions_asked >= MIN_ASSESSMENT_EXCHANGES):
        farewell = parsed.get("reply", "") if parsed and parsed.get("reply", "").strip() else (
            "Thank you so much for the chat! Now let's try something a little different."
        )
        db.add(_assistant_message(assessment.id, farewell, KIND_CHAT))

        # Transition to the audio-only listening section.
        assessment.phase = PHASE_LISTENING
        assessment.section_step = 0
        await db.flush()
        first_item = LISTENING_ITEMS[0]
        db.add(_assistant_message(assessment.id, first_item.tutor_text, KIND_LISTENING))
        await db.flush()
        assessment = await _get_assessment_with_messages(db, assessment.id)
        resp = AssessmentResponse.model_validate(assessment)
        resp.is_complete = False
        return resp

    reply = parsed.get("reply", "") if parsed else ""
    db.add(_assistant_message(assessment.id, reply, KIND_CHAT))
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = False
    return resp


async def _handle_listening(
    db: AsyncSession, current_user: User, assessment: Assessment, body: AssessmentMessageCreate
) -> AssessmentResponse:
    step = assessment.section_step
    item = _item_for_step(PHASE_LISTENING, step)
    if item is None:
        # Bank exhausted but the phase never advanced (edge case) — advance now.
        return await _advance_to_speaking(db, current_user, assessment)

    grading = await _grade_listening_answer(db, current_user, item, body.text)
    db.add(_user_message(assessment.id, body.text, KIND_LISTENING, body.source, {**body.metrics, **grading} if body.metrics else grading))

    # Re-count answered listening items from scratch — the section_step is
    # derived from message history so retries can't double-advance it.
    answered = [
        m for m in await _refresh_messages(db, assessment.id)
        if m.role == "user" and m.kind == KIND_LISTENING
    ]
    consecutive_fails = 0
    for m in reversed(answered):
        metrics = json.loads(m.metrics or "{}")
        if metrics.get("correct") == 1:
            break
        consecutive_fails += 1

    if len(answered) >= len(LISTENING_ITEMS) or consecutive_fails >= LISTENING_EARLY_STOP_FAILS:
        return await _advance_to_speaking(db, current_user, assessment)

    assessment.section_step = len(answered)
    await db.flush()
    next_item = LISTENING_ITEMS[assessment.section_step]
    db.add(_assistant_message(assessment.id, next_item.tutor_text, KIND_LISTENING))
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = False
    return resp


async def _advance_to_speaking(
    db: AsyncSession, current_user: User, assessment: Assessment
) -> AssessmentResponse:
    assessment.phase = PHASE_SPEAKING
    assessment.section_step = 0
    await db.flush()
    first_item = SPEAKING_ITEMS[0]
    db.add(_assistant_message(
        assessment.id,
        f"{first_item.text}\n\nFocus: {first_item.focus}",
        KIND_SPEAKING,
    ))
    await db.flush()
    assessment = await _get_assessment_with_messages(db, assessment.id)
    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = False
    return resp


async def _handle_speaking(
    db: AsyncSession, current_user: User, assessment: Assessment, body: AssessmentMessageCreate
) -> AssessmentResponse:
    step = assessment.section_step
    item = _item_for_step(PHASE_SPEAKING, step)
    if item is None:
        raise HTTPException(status_code=400, detail="Speaking section already finished")

    # Server re-computes metrics from the transcript when the client didn't
    # send them (e.g. typed fallback) — WER/PER are deterministic anyway.
    metrics = body.metrics
    if not metrics or "composite" not in metrics:
        metrics = score_pronunciation(item.text, body.text, None)
    db.add(_user_message(assessment.id, body.text, KIND_SPEAKING, body.source, metrics))

    answered = [
        m for m in await _refresh_messages(db, assessment.id)
        if m.role == "user" and m.kind == KIND_SPEAKING
    ]
    if len(answered) >= len(SPEAKING_ITEMS):
        # Every section done — the client should now call /complete.
        db.add(_assistant_message(
            assessment.id,
            "Perfect, that's everything! Let me put your results together.",
            KIND_CHAT,
        ))
        await db.flush()
        assessment = await _get_assessment_with_messages(db, assessment.id)
        resp = AssessmentResponse.model_validate(assessment)
        resp.is_complete = True
        return resp

    assessment.section_step = len(answered)
    await db.flush()
    next_item = SPEAKING_ITEMS[assessment.section_step]
    db.add(_assistant_message(
        assessment.id,
        f"{next_item.text}\n\nFocus: {next_item.focus}",
        KIND_SPEAKING,
    ))
    await db.flush()

    assessment = await _get_assessment_with_messages(db, assessment.id)
    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = False
    return resp


async def _refresh_messages(db: AsyncSession, assessment_id: str) -> list[AssessmentMessage]:
    result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment_id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    return list(result.scalars().all())


async def _analyze_assessment(db: AsyncSession, current_user: User, assessment: Assessment) -> None:
    """Run the assessment analysis and persist the results (level, confidence,
    dimension scores, strengths, weaknesses, recommendations, summary).

    Dimension scores for listening/pronunciation are computed deterministically
    from graded items / pronunciation metrics; the LLM scores the conversation
    dimensions with a rubric and writes the qualitative fields. The final
    level and confidence are aggregated deterministically — the LLM is never
    allowed to invent a level or claim unaudited dimensions.
    """
    msg_result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment.id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    messages = msg_result.scalars().all()

    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="Assessment has too few messages to evaluate")

    # --- Deterministic dimensions from stored evidence ---
    listening_correct: list[float] = []
    for m in messages:
        if m.role == "user" and m.kind == KIND_LISTENING and m.metrics:
            try:
                listening_correct.append(100.0 if json.loads(m.metrics).get("correct") == 1 else 0.0)
            except Exception:
                pass
    listening_dim = mean_or_none(listening_correct)

    pron_scores: list[float] = []
    for m in messages:
        if m.role == "user" and m.kind == KIND_SPEAKING and m.metrics:
            try:
                composite = json.loads(m.metrics).get("composite")
                score = _clamp_score(composite)
                if score is not None:
                    pron_scores.append(score)
            except Exception:
                pass
    pronunciation_dim = mean_or_none(pron_scores)

    # --- LLM: conversation dims + qualitative fields ---
    conversation_text = "\n".join(f"{m.role.upper()}: {m.text}" for m in messages)
    n_exchanges = _conversation_questions_asked(messages)
    evidence_block = (
        "\n\nOBJECTIVELY MEASURED DIMENSIONS (fixed data — do not re-estimate):\n"
        f"- listening: {listening_dim if listening_dim is not None else 'NOT ASSESSED'}"
        f" ({len(listening_correct)} items)\n"
        f"- pronunciation: {pronunciation_dim if pronunciation_dim is not None else 'NOT ASSESSED'}"
        f" ({len(pron_scores)} recordings)\n"
    )

    llm = LLMRouter(db, current_user.id)
    raw_analysis = ""
    analysis_result = None
    # Retry up to 3 times when the model returns empty content — smaller/flash
    # models occasionally produce blank responses for structured prompts.
    for attempt in range(3):
        try:
            analysis_result = await llm.complete_with_fallback(
                messages=[{
                    "role": "user",
                    "content": f"{ASSESSMENT_ANALYSIS_PROMPT}{evidence_block}\n\nCONVERSATION:\n{conversation_text}",
                }],
                system_prompt="You are a CEFR assessor. Analyze the conversation and return valid JSON only.",
                task="assessment",
                temperature=0.3,
                max_tokens=2048,
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

    # The parsed shape must actually look like an analysis — the legacy
    # conversational fallback ({"reply": ...}) would otherwise silently save
    # empty result cards (blank summary/strengths/weaknesses).
    if not parsed.get("summary"):
        logger.error(
            f"Assessment analysis for assessment {assessment.id} returned an unexpected shape: "
            f"keys={list(parsed.keys())}, raw={raw_analysis[:500]}"
        )
        raise HTTPException(status_code=503, detail="Failed to analyze the assessment. Please try again.")

    # --- Aggregation (deterministic) ---
    dimension_scores: dict[str, float | None] = {
        "grammar": _clamp_score(parsed.get("grammar")),
        "vocabulary": _clamp_score(parsed.get("vocabulary")),
        "fluency": _clamp_score(parsed.get("fluency")),
        "listening": listening_dim,
        "pronunciation": pronunciation_dim,
    }
    level = final_level(dimension_scores)
    if level is None:
        # No dimension could be scored (LLM failed the rubric and no audio
        # evidence) — fall back to the user's preferred CEFR level so we don't
        # crash on the NOT NULL VARCHAR(2) column.
        cefr_setting = await db.execute(
            select(Setting).where(Setting.user_id == current_user.id, Setting.key == "default_cefr")
        )
        preferred = cefr_setting.scalar_one_or_none()
        preferred_level = (preferred.value or "").strip().upper() if preferred else ""
        level = (
            preferred_level
            if preferred_level in _CEFR_LEVELS
            else current_user.current_level
            if current_user.current_level in _CEFR_LEVELS
            else "A1"
        )
        logger.warning(
            f"Assessment {assessment.id} produced no scoreable dimension, "
            f"falling back to {level}"
        )

    confidence = compute_confidence(
        dimension_scores,
        n_exchanges=n_exchanges,
        n_listening=len(listening_correct),
        n_speaking=len(pron_scores),
    )
    strengths, weaknesses = strengths_weaknesses(dimension_scores)

    assessment.estimated_level = level
    assessment.confidence = confidence
    assessment.dimension_scores = json.dumps(dimension_scores, ensure_ascii=False)
    assessment.strengths = _coerce_json_list(strengths)
    assessment.weaknesses = _coerce_json_list(weaknesses)
    assessment.recommendations = _coerce_json_list(parsed.get("recommendations"))
    assessment.summary = parsed.get("summary", "")

    # Update user level and mark assessment completed
    current_user.current_level = assessment.estimated_level
    current_user.assessment_completed = True

    await db.flush()

    logger.info(
        f"Assessment {assessment.id} analysis persisted for user {current_user.id}: "
        f"level={assessment.estimated_level}, confidence={assessment.confidence}, "
        f"dimension_scores={assessment.dimension_scores}, "
        f"strengths={len(json.loads(assessment.strengths or '[]'))}, "
        f"weaknesses={len(json.loads(assessment.weaknesses or '[]'))}"
    )


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

    await _analyze_assessment(db, current_user, assessment)

    assessment.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return await _get_assessment_with_messages(db, assessment.id)


@router.post("/{assessment_id}/reanalyze", response_model=AssessmentResponse)
async def reanalyze_assessment(
    assessment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Each reanalyze triggers up to 3 LLM calls — throttle repeat clicks.
    # Per-process guard (single-worker deployments), not a distributed limit.
    now = datetime.now(timezone.utc)
    last = _reanalyze_last.get(assessment_id)
    if last and (now - last) < _REANALYZE_COOLDOWN:
        raise HTTPException(status_code=429, detail="Please wait a moment before re-analyzing again")
    _reanalyze_last[assessment_id] = now

    result = await db.execute(
        select(Assessment).where(Assessment.id == assessment_id, Assessment.user_id == current_user.id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    if not assessment.completed_at:
        raise HTTPException(status_code=400, detail="Assessment is not complete yet")

    # Re-run the LLM analysis over the stored conversation — refreshes the
    # summary/strengths/weaknesses/recommendations (and estimated level) in
    # place, e.g. when the original analysis saved empty results.
    await _analyze_assessment(db, current_user, assessment)

    return await _get_assessment_with_messages(db, assessment.id)
