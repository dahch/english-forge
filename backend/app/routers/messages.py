from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import (
    Correction,
    Message,
    Scenario,
    Session,
    User,
    VocabItem,
    ProviderConfig,
    ProgressDaily,
)
from app.schemas.session import (
    ConversationTurnResponse,
    CorrectionResponse,
    MessageCreate,
    MessageResponse,
)
from app.llm.router import LLMRouter, parse_llm_json
from app.llm.prompts import build_system_prompt
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.srs.sm2 import next_review_date
from app.utils import get_tutor_profile_dict, resolve_tts_voice

router = APIRouter(prefix="/api/sessions", tags=["messages"])


@router.post("/{session_id}/messages", response_model=ConversationTurnResponse)
async def send_message(
    session_id: str,
    body: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.ended_at:
        raise HTTPException(status_code=400, detail="Session has ended")

    user_msg = Message(session_id=session_id, role="user", text=body.text)
    db.add(user_msg)
    await db.flush()

    history_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    history = history_result.scalars().all()

    messages_for_llm = []
    for m in history:
        messages_for_llm.append({"role": m.role, "content": m.text})

    scenario_prompt = None
    scenario_key = None
    if session.scenario_id:
        sc_result = await db.execute(select(Scenario).where(Scenario.id == session.scenario_id))
        scenario = sc_result.scalar_one_or_none()
        if scenario:
            if scenario.is_custom:
                scenario_prompt = scenario.system_prompt
            else:
                scenario_key = scenario.name.lower().replace(" ", "_").replace("-", "_")

    profile_dict = await get_tutor_profile_dict(db, current_user.id)

    system_prompt = build_system_prompt(
        scenario_key=scenario_key,
        scenario_custom_prompt=scenario_prompt,
        cefr_level=session.cefr_level,
        tutor_profile=profile_dict,
    )

    llm_router = LLMRouter(db, current_user.id)
    llm_result = await llm_router.complete_with_fallback(
        messages=messages_for_llm,
        system_prompt=system_prompt,
        task="conversation",
    )

    parsed = parse_llm_json(llm_result["content"])

    session.provider_used = llm_result.get("provider", "unknown")

    assistant_msg = Message(
        session_id=session_id,
        role="assistant",
        text=parsed.get("reply", ""),
    )
    db.add(assistant_msg)
    await db.flush()

    corrections_data = parsed.get("corrections", [])
    correction_responses = []
    for c in corrections_data:
        corr = Correction(
            message_id=user_msg.id,
            error_type=c.get("error_type", "unknown"),
            original_fragment=c.get("original", ""),
            correction=c.get("correction", ""),
            explanation=c.get("explanation", ""),
        )
        db.add(corr)
        await db.flush()
        correction_responses.append(CorrectionResponse(
            id=corr.id,
            error_type=corr.error_type,
            original_fragment=corr.original_fragment,
            correction=corr.correction,
            explanation=corr.explanation,
        ))

    new_vocab_data = parsed.get("new_vocab", [])
    new_vocab_list = []
    for v in new_vocab_data:
        existing = await db.execute(
            select(VocabItem).where(
                VocabItem.user_id == current_user.id,
                VocabItem.word == v.get("word", ""),
            )
        )
        if existing.scalar_one_or_none():
            continue

        vocab = VocabItem(
            user_id=current_user.id,
            word=v.get("word", ""),
            definition=v.get("definition", ""),
            example=v.get("example", ""),
            source_message_id=assistant_msg.id,
            ease_factor=2.5,
            interval_days=0,
            next_review_at=next_review_date(0),
        )
        db.add(vocab)
        new_vocab_list.append({
            "word": vocab.word,
            "definition": vocab.definition,
            "example": vocab.example,
        })

    audio_url = None
    try:
        tts = TTSPersonalAPI()
        voice = await resolve_tts_voice(db, current_user.id)
        audio_bytes = await tts.synthesize(parsed.get("reply", ""), voice=voice)
        if audio_bytes:
            import base64
            audio_url = f"data:audio/mpeg;base64,{base64.b64encode(audio_bytes).decode()}"
            assistant_msg.audio_url = audio_url
    except Exception:
        pass

    await db.flush()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    user_message_response = MessageResponse(
        id=user_msg.id,
        session_id=user_msg.session_id,
        role=user_msg.role,
        text=user_msg.text,
        audio_url=user_msg.audio_url,
        created_at=user_msg.created_at,
        corrections=correction_responses,
    )

    return ConversationTurnResponse(
        user_message=user_message_response,
        assistant_message=MessageResponse(
            id=assistant_msg.id,
            session_id=assistant_msg.session_id,
            role=assistant_msg.role,
            text=assistant_msg.text,
            audio_url=assistant_msg.audio_url,
            created_at=assistant_msg.created_at,
            # Corrections attach to the user message; duplicated here only so
            # legacy clients reading assistant_message.corrections keep working.
            corrections=correction_responses,
        ),
        corrections=correction_responses,
        new_vocab=new_vocab_list,
        audio_url=audio_url,
    )


@router.get("/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Session not found")

    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    responses = []
    for m in messages:
        corr_result = await db.execute(
            select(Correction).where(Correction.message_id == m.id)
        )
        corrections = corr_result.scalars().all()
        responses.append(MessageResponse(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            text=m.text,
            audio_url=m.audio_url,
            created_at=m.created_at,
            corrections=[
                CorrectionResponse(
                    id=c.id,
                    error_type=c.error_type,
                    original_fragment=c.original_fragment,
                    correction=c.correction,
                    explanation=c.explanation,
                )
                for c in corrections
            ],
        ))
    return responses
