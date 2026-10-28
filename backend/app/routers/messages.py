from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.concurrency import session_turn_lock
from app.database import get_db
from app.dependencies import get_current_user
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.llm.prompts import build_system_prompt
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import (
    Correction,
    Message,
    Scenario,
    Session,
    User,
    VocabItem,
)
from app.schemas.session import (
    ConversationTurnResponse,
    CorrectionResponse,
    MessageCreate,
    MessageResponse,
)
from app.services.media_store import read_audio, store_audio
from app.srs.sm2 import next_review_date
from app.utils import bump_daily_words, get_tutor_profile_dict, resolve_tts_voice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["messages"])

_FALLBACK_REPLY = (
    "Sorry, I lost my train of thought for a second — tell me a bit more about that."
)


@router.post("/{session_id}/messages", response_model=ConversationTurnResponse)
async def send_message(
    session_id: str,
    body: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.ended_at:
        raise HTTPException(status_code=400, detail="Session has ended")

    # One turn per session at a time: the read-history → LLM → persist sequence
    # must not interleave, or two tabs double-post and the replies land in an
    # arbitrary order.
    async with session_turn_lock(session_id):
        parsed, provider_used, profile_dict = await _generate_reply(
            db, current_user, session, text
        )
        reply = (parsed.get("reply") or "").strip() or _FALLBACK_REPLY

        (
            user_msg,
            assistant_msg,
            correction_responses,
            new_vocab_list,
        ) = await _persist_turn(db, current_user, session, text, reply, parsed, provider_used)

        # Release the write transaction BEFORE talking to the TTS service: the
        # previous code held SQLite's single writer for the whole synthesis
        # (up to 30s of polling), blocking every other write in the process.
        await db.commit()

    audio_url = await _attach_audio(db, current_user, assistant_msg, profile_dict)

    return ConversationTurnResponse(
        user_message=_message_response(user_msg, correction_responses),
        assistant_message=_message_response(assistant_msg, correction_responses),
        corrections=correction_responses,
        new_vocab=new_vocab_list,
        # Audio is fetched lazily from the authenticated endpoint; the turn
        # never carries a base64 blob.
        audio_url=None,
    )


async def _generate_reply(
    db: AsyncSession,
    current_user: User,
    session: Session,
    text: str,
) -> tuple[dict, str, dict | None]:
    """Build the prompt and call the LLM. Read-only: no writes before the call."""
    history_result = await db.execute(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    )
    history = history_result.scalars().all()
    messages_for_llm = [{"role": m.role, "content": m.text} for m in history]
    messages_for_llm.append({"role": "user", "content": text})

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
    parsed: dict = {}
    provider_used = "unknown"
    try:
        llm_result = await llm_router.complete_with_fallback(
            messages=messages_for_llm,
            system_prompt=system_prompt,
            task="conversation",
        )
        parsed = parse_llm_json(llm_result["content"])
        provider_used = llm_result.get("provider", "unknown")
    except Exception as e:  # noqa: BLE001 - degraded reply beats a failed turn
        logger.error("Conversation LLM call failed for session %s: %s", session.id, e)

    return parsed, provider_used, profile_dict


async def _persist_turn(
    db: AsyncSession,
    current_user: User,
    session: Session,
    text: str,
    reply: str,
    parsed: dict,
    provider_used: str,
) -> tuple[Message, Message, list[CorrectionResponse], list[dict]]:
    """Write the whole turn in one short transaction.

    Timestamps are assigned in Python with microsecond precision so the user
    message always sorts before the assistant reply, even within the same
    second (SQLite's func.now() only has second resolution).
    """
    now = datetime.now(timezone.utc)
    user_msg = Message(session_id=session.id, role="user", text=text, created_at=now)
    # +1µs guarantees the reply sorts after the user message even when both are
    # inserted in the same statement batch.
    assistant_msg = Message(
        session_id=session.id,
        role="assistant",
        text=reply,
        created_at=now + timedelta(microseconds=1),
    )
    session.provider_used = provider_used
    db.add_all([user_msg, assistant_msg])
    await db.flush()

    corrections: list[Correction] = []
    for raw in parsed.get("corrections") or []:
        if not isinstance(raw, dict):
            continue
        correction = Correction(
            message_id=user_msg.id,
            error_type=str(raw.get("error_type") or "unknown")[:100],
            original_fragment=str(raw.get("original") or ""),
            correction=str(raw.get("correction") or ""),
            explanation=str(raw.get("explanation") or ""),
        )
        db.add(correction)
        corrections.append(correction)

    new_vocab_list = await _persist_vocab(db, current_user.id, assistant_msg, parsed)
    if new_vocab_list:
        await bump_daily_words(db, current_user.id, len(new_vocab_list))

    # Flush once so generated ids exist before building the response objects.
    await db.flush()
    correction_responses = [
        CorrectionResponse(
            id=c.id,
            error_type=c.error_type,
            original_fragment=c.original_fragment,
            correction=c.correction,
            explanation=c.explanation,
        )
        for c in corrections
    ]
    return user_msg, assistant_msg, correction_responses, new_vocab_list


async def _persist_vocab(
    db: AsyncSession,
    user_id: str,
    assistant_msg: Message,
    parsed: dict,
) -> list[dict]:
    """Insert new vocabulary, deduplicated in one query instead of N."""
    candidates: dict[str, dict] = {}
    for raw in parsed.get("new_vocab") or []:
        if not isinstance(raw, dict):
            continue
        word = str(raw.get("word") or "").strip()
        if not word:
            continue
        key = word.lower()
        if key not in candidates:
            candidates[key] = {
                "word": word,
                "definition": str(raw.get("definition") or "").strip(),
                "example": str(raw.get("example") or "").strip(),
            }

    if not candidates:
        return []

    # Only fetch the candidate words, not the whole collection: a mature
    # vocabulary list is thousands of rows and this runs on every turn.
    existing_result = await db.execute(
        select(VocabItem.word).where(
            VocabItem.user_id == user_id,
            func.lower(VocabItem.word).in_(list(candidates)),
        )
    )
    existing = {w.lower() for w in existing_result.scalars().all() if w}

    created: list[dict] = []
    for key, item in candidates.items():
        if key in existing:
            continue
        # Per-item SAVEPOINT: the unique index (user_id, lower(word)) closes the
        # SELECT-then-INSERT race, and the loser skips this word instead of
        # failing the whole conversation turn.
        try:
            async with db.begin_nested():
                db.add(
                    VocabItem(
                        user_id=user_id,
                        word=item["word"],
                        definition=item["definition"],
                        example=item["example"],
                        source_message_id=assistant_msg.id,
                        ease_factor=2.5,
                        interval_days=0,
                        next_review_at=next_review_date(0),
                    )
                )
                await db.flush()
        except IntegrityError:
            logger.info("Skipped duplicate vocabulary word %r", item["word"])
            continue
        existing.add(key)
        created.append(item)
    return created


async def _attach_audio(
    db: AsyncSession,
    current_user: User,
    assistant_msg: Message,
    profile_dict: dict | None,
) -> str | None:
    """Synthesize the reply audio after the turn is committed.

    A TTS outage must never fail or roll back the conversation, so failures are
    logged and simply leave the client to fall back to browser speech.
    """
    try:
        tts = TTSPersonalAPI()
        voice = await resolve_tts_voice(db, current_user.id, profile_dict)
        audio_bytes = await tts.synthesize(assistant_msg.text, voice=voice)
    except Exception as e:  # noqa: BLE001 - audio is best-effort
        logger.warning("TTS synthesis failed for message %s: %s", assistant_msg.id, e)
        return None

    if not audio_bytes:
        return None
    assistant_msg.audio_url = store_audio(audio_bytes)
    await db.commit()
    return None


def _message_response(msg: Message, corrections: list[CorrectionResponse]) -> MessageResponse:
    # Pass the storage reference so the response validator can derive
    # has_audio; the serializer never exposes it.
    return MessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        text=msg.text,
        audio_url=msg.audio_url,
        created_at=msg.created_at,
        corrections=corrections,
    )


@router.get("/{session_id}/messages/{message_id}/audio")
async def get_message_audio(
    session_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve the tutor audio for one message (cookie-authenticated)."""
    session_result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    if not session_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Session not found")

    message = (
        await db.execute(
            select(Message).where(
                Message.id == message_id,
                Message.session_id == session_id,
                Message.role == "assistant",
            )
        )
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    audio = read_audio(message.audio_url)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not available")
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=86400"},
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
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    corr_result = await db.execute(
        select(Correction)
        .join(Message, Correction.message_id == Message.id)
        .where(Message.session_id == session_id)
        .order_by(Correction.id)
    )
    corrections_by_message: dict[str, list[CorrectionResponse]] = {}
    for c in corr_result.scalars().all():
        corrections_by_message.setdefault(c.message_id, []).append(
            CorrectionResponse(
                id=c.id,
                error_type=c.error_type,
                original_fragment=c.original_fragment,
                correction=c.correction,
                explanation=c.explanation,
            )
        )

    return [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            text=m.text,
            audio_url=m.audio_url,
            created_at=m.created_at,
            corrections=corrections_by_message.get(m.id, []),
        )
        for m in messages
    ]
