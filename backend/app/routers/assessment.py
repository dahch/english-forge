"""HTTP adapter for the multi-skill placement assessment.

All domain logic (phase state machine, grading, pronunciation scoring,
analysis) lives in app.services.assessment_flow — this module only maps HTTP
bodies to that service and back.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Assessment, AssessmentMessage, User
from app.services.assessment_flow import (
    PHASE_MIC_CHECK,
    analyze_assessment,
    ensure_message_audio,
    expected_text_for,
    handle_message,
    handle_start,
    reload_assessment,
)
from app.services.pronunciation import score_pronunciation_async
from app.services.stt import transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assessment", tags=["assessment"])

# Reanalyze cost guard: cooldown between LLM analyses of the same assessment.
_REANALYZE_COOLDOWN = timedelta(seconds=30)
_reanalyze_last: dict[str, datetime] = {}

# Maximum accepted recording upload (30s of opus/webm is well under this).
_MAX_RECORDING_BYTES = 10 * 1024 * 1024


class AssessmentMessageCreate(BaseModel):
    # max_length keeps a single turn from writing unbounded text to the DB.
    text: str = Field(..., min_length=1, max_length=2000)
    # "voice" when the text came from /recordings (STT), "text" when typed.
    source: str = Field("text", pattern="^(text|voice)$")
    # STT word timestamps echoed back from /recordings — the ONLY client input
    # to pronunciation scoring. The server recomputes all metrics itself.
    words: list[dict] | None = None
    # The banked item the client believes it is answering; the server drops
    # submissions that don't match the current item (stale/duplicate).
    item_id: str | None = None


class AssessmentMessageResponse(BaseModel):
    id: str
    role: str
    text: str
    kind: str
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
    # Pronunciation metrics the server computed — informational; the client
    # echoes `words` back on /message and the server recomputes from scratch.
    metrics: dict | None = None


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
    return await handle_start(db, assessment)


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

    audio = await ensure_message_audio(db, current_user, assessment, msg)
    if not audio:
        raise HTTPException(status_code=503, detail="Audio generation is unavailable right now")
    return Response(content=audio, media_type="audio/mpeg")


async def _read_limited(file: UploadFile, max_bytes: int) -> tuple[bytes, bool]:
    """Read an upload in chunks, aborting as soon as it exceeds max_bytes.

    Reading the whole body up-front would buffer unbounded data from a slow or
    malformed client; chunking lets an oversize recording fail fast without
    holding a greenlet on the remaining bytes.
    """
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            return b"", True
        chunks.append(chunk)
    return b"".join(chunks), False


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

    audio, too_large = await _read_limited(file, _MAX_RECORDING_BYTES)
    if too_large:
        raise HTTPException(status_code=413, detail="Recording too large (max 10MB)")
    if not audio:
        raise HTTPException(status_code=400, detail="Empty recording")

    content_type = file.content_type or "audio/webm"
    stt = await transcribe_audio(audio, content_type)

    expected = expected_text_for(assessment.phase, assessment.section_step)
    metrics = None
    if expected and stt["text"].strip():
        metrics = await score_pronunciation_async(expected, stt["text"], stt["words"])
    return RecordingResponse(transcript=stt["text"], words=stt["words"], metrics=metrics)


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

    try:
        assessment, is_complete = await handle_message(
            db,
            current_user,
            assessment,
            text=body.text,
            source=body.source,
            words=body.words,
            item_id=body.item_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    resp = AssessmentResponse.model_validate(assessment)
    resp.is_complete = is_complete
    return resp


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
        return await reload_assessment(db, assessment.id)

    # Evidence integrity: an assessment that never left the mic check has no
    # measured evidence at all. Reject /complete so a bare start→complete can't
    # lock in a default level from an empty analysis.
    if assessment.phase == PHASE_MIC_CHECK:
        raise HTTPException(
            status_code=400,
            detail="Answer the mic check before finishing the assessment",
        )

    try:
        await analyze_assessment(db, current_user, assessment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    assessment.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return await reload_assessment(db, assessment.id)


@router.post("/{assessment_id}/reanalyze", response_model=AssessmentResponse)
async def reanalyze_assessment(
    assessment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Each reanalyze triggers up to 3 LLM calls — throttle repeat clicks.
    # Per-process guard (single-worker deployments), not a distributed limit.
    now = datetime.now(timezone.utc)
    # Bound the guard so a long-lived process doesn't grow it unbounded (one
    # entry per assessment id, otherwise never evicted).
    if len(_reanalyze_last) > 256:
        cutoff = now - _REANALYZE_COOLDOWN
        _reanalyze_last = {k: v for k, v in _reanalyze_last.items() if v >= cutoff}
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
    try:
        await analyze_assessment(db, current_user, assessment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return await reload_assessment(db, assessment.id)
