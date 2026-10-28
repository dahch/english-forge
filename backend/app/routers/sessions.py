from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Scenario, Session, Message, User, Correction, VocabItem
from app.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionSummaryResponse,
    CorrectionResponse,
    MessageResponse,
)
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.services.session_metrics import finalize_session
from app.services.stt import transcribe_audio
from app.utils import get_tutor_profile_dict, resolve_tts_voice
from app.llm.router import LLMRouter

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)

MAX_RECORDING_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/wav",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4a",
}

SCENARIO_OPENING_GREETINGS: dict[str, str] = {
    "job_interview": "Hello! Thank you for coming in today. To get started, could you please introduce yourself and tell me a bit about your background?",
    "ordering_food": "Good evening! Welcome to our restaurant. Can I start you off with something to drink, or are you ready to order?",
    "hotel_checkin": "Welcome to our hotel! Are you checking in today? Could I please have your name or reservation details?",
    "small_talk": "Hi there! It's so nice to meet you. How is your day going so far?",
    "business_meeting": "Good morning everyone. Thanks for joining on time. Shall we dive right into our agenda and review the project updates?",
    "phone_call": "Thank you for calling customer support. My name is {tutor_name}. How may I help you today?",
    "free_talk": "Hello! I'm {tutor_name}. It's great to chat with you today. What's on your mind, or what topic would you like to explore?",
}


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scenario = None
    scenario_name = None
    if body.scenario_id:
        result = await db.execute(
            select(Scenario).where(
                Scenario.id == body.scenario_id,
                (Scenario.user_id.is_(None)) | (Scenario.user_id == current_user.id),
            )
        )
        scenario = result.scalar_one_or_none()
        if not scenario:
            raise HTTPException(status_code=404, detail="Scenario not found")
        scenario_name = scenario.name

    profile_dict = await get_tutor_profile_dict(db, current_user.id)
    tutor_name = (profile_dict.get("name") if profile_dict else "Sarah") or "Sarah"
    tutor_name = tutor_name.strip()

    greeting: str | None = None
    if scenario is None:
        greeting = SCENARIO_OPENING_GREETINGS["free_talk"].format(tutor_name=tutor_name)
    else:
        scenario_key = scenario.name.lower().replace(" ", "_").replace("-", "_")
        if not scenario.is_custom and scenario_key in SCENARIO_OPENING_GREETINGS:
            greeting = SCENARIO_OPENING_GREETINGS[scenario_key].format(tutor_name=tutor_name)
        elif scenario.is_custom:
            try:
                llm = LLMRouter(db, current_user.id)
                llm_res = await llm.complete_with_fallback(
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Generate a natural 1-2 sentence opening line in character to start this roleplay scenario: '{scenario.name}'. "
                            f"Scenario context: '{scenario.system_prompt}'. Speak directly to the student without quotation marks or meta-commentary."
                        )
                    }],
                    system_prompt=f"You are a roleplay partner for '{scenario.name}'. Start the conversation in character.",
                    task="conversation",
                    temperature=0.7,
                    max_tokens=80,
                )
                opening = (llm_res.get("content") or "").strip().strip('"')
                if opening:
                    greeting = opening
            except Exception as e:
                logger.warning(f"LLM opening generation failed for custom scenario '{scenario.name}': {e}")

            if not greeting:
                greeting = f"Hello! Welcome to our {scenario.name} session. Whenever you're ready, let's begin!"
        else:
            greeting = SCENARIO_OPENING_GREETINGS.get(scenario_key, f"Hello! Welcome to our {scenario.name} session. Let's begin!")

    session = Session(
        user_id=current_user.id,
        scenario_id=body.scenario_id,
        cefr_level=body.cefr_level,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    initial_msg = Message(session_id=session.id, role="assistant", text=greeting)
    db.add(initial_msg)
    await db.flush()

    # Commit session+greeting BEFORE the TTS round-trip. Synthesis polls
    # personal-api for up to TTS_JOB_TIMEOUT_SECONDS; holding SQLite's single
    # writer for that long would block every other write in the process.
    await db.commit()

    try:
        tts = TTSPersonalAPI()
        voice = await resolve_tts_voice(db, current_user.id, profile_dict)
        audio_bytes = await tts.synthesize(greeting, voice=voice)
        if audio_bytes:
            import base64
            initial_msg.audio_url = f"data:audio/mpeg;base64,{base64.b64encode(audio_bytes).decode()}"
            await db.commit()
    except Exception as e:
        logger.warning(f"TTS synthesis failed for opening session greeting: {e}")

    # Re-select so the response carries the stored audio_url (if any).
    msg_result = await db.execute(
        select(Message)
        .options(selectinload(Message.corrections))
        .where(Message.id == initial_msg.id)
    )
    greeting_msg = msg_result.scalar_one()

    resp = SessionResponse(
        id=session.id,
        scenario_id=session.scenario_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        cefr_level=session.cefr_level,
        provider_used=session.provider_used,
        scenario_name=scenario_name,
        message_count=1,
        initial_message=MessageResponse.model_validate(greeting_msg),
    )
    return resp


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(
            Session,
            func.count(Message.id).label("message_count"),
            Scenario.name.label("scenario_name"),
        )
        .outerjoin(Scenario, Session.scenario_id == Scenario.id)
        .outerjoin(Message, Message.session_id == Session.id)
        .where(Session.user_id == current_user.id)
        .group_by(Session.id, Scenario.name)
        .order_by(Session.started_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(query)).all()
    return [
        SessionResponse(
            id=s.id,
            scenario_id=s.scenario_id,
            started_at=s.started_at,
            ended_at=s.ended_at,
            cefr_level=s.cefr_level,
            provider_used=s.provider_used,
            scenario_name=scenario_name,
            message_count=count,
        )
        for s, count, scenario_name in rows
    ]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_count = await db.execute(
        select(func.count(Message.id)).where(Message.session_id == session.id)
    )
    count = msg_count.scalar() or 0

    scenario_name = None
    if session.scenario_id:
        sc = await db.execute(select(Scenario.name).where(Scenario.id == session.scenario_id))
        scenario_name = sc.scalar_one_or_none()

    return SessionResponse(
        id=session.id,
        scenario_id=session.scenario_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        cefr_level=session.cefr_level,
        provider_used=session.provider_used,
        scenario_name=scenario_name,
        message_count=count,
    )


@router.patch("/{session_id}/end", response_model=SessionSummaryResponse)
async def end_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # End + bill in one idempotent step: a second PATCH (two tabs, retried
    # request) must not add the same minutes to ProgressDaily again.
    duration = await finalize_session(db, session)

    msg_result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    corr_result = await db.execute(
        select(Correction)
        .join(Message, Correction.message_id == Message.id)
        .where(Message.session_id == session_id)
    )
    corrections = corr_result.scalars().all()

    corrections_by_type: dict[str, int] = {}
    corrections_list = []
    for c in corrections:
        corrections_by_type[c.error_type] = corrections_by_type.get(c.error_type, 0) + 1
        corrections_list.append(CorrectionResponse(
            id=c.id,
            error_type=c.error_type,
            original_fragment=c.original_fragment,
            correction=c.correction,
            explanation=c.explanation,
        ))

    vocab_result = await db.execute(
        select(VocabItem).where(VocabItem.source_message_id.in_(
            [m.id for m in messages]
        ))
    )
    new_vocab_items = vocab_result.scalars().all()
    new_vocab_list = [
        {"word": v.word, "definition": v.definition, "example": v.example}
        for v in new_vocab_items
    ]

    await db.flush()

    return SessionSummaryResponse(
        session_id=session_id,
        duration_minutes=round(duration, 1),
        total_messages=len(messages),
        corrections_by_type=corrections_by_type,
        total_corrections=len(corrections),
        new_words_learned=len(new_vocab_items),
        corrections_list=corrections_list,
        new_vocab_list=new_vocab_list,
    )


@router.post("/{session_id}/recordings")
async def upload_recording(
    session_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.ended_at is not None:
        raise HTTPException(status_code=400, detail="Session has ended")

    chunk = await file.read(MAX_RECORDING_BYTES + 1)
    if len(chunk) > MAX_RECORDING_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 10MB.")
    if not chunk:
        raise HTTPException(status_code=400, detail="Empty recording.")
    audio_bytes = chunk

    raw_content_type = (file.content_type or "").strip()
    base_content_type = raw_content_type.split(";")[0].strip().lower()
    if base_content_type in ALLOWED_AUDIO_TYPES:
        content_type = raw_content_type
    elif base_content_type.startswith("audio/"):
        content_type = "audio/webm"
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type '{raw_content_type}'. Allowed formats: {', '.join(sorted(ALLOWED_AUDIO_TYPES))}",
        )

    stt = await transcribe_audio(audio_bytes, content_type=content_type)
    return {
        "transcript": stt.get("text", ""),
        "words": stt.get("words", []),
    }

