from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.models import Message, Session, User
from app.security import decode_access_token
from app.llm.router import LLMRouter, parse_llm_json
from app.llm.prompts import build_system_prompt
from app.models.models import Correction, Scenario
from app.utils import bump_daily_minutes, get_tutor_profile_dict, resolve_tts_voice
from app.integrations.tts_personal_api import TTSPersonalAPI
from app.integrations.stt_personal_api import STTPersonalAPI
from app.integrations.stt_whisper_server import STTWhisperServer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


async def _get_user_from_token(token: str) -> User | None:
    payload = decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


@router.websocket("/api/ws/session/{session_id}")
async def websocket_session(websocket: WebSocket, session_id: str, token: str = ""):
    user = await _get_user_from_token(token)
    if not user:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    async with async_session_factory() as db:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user.id)
        )
        session = result.scalar_one_or_none()
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return

    await websocket.accept()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    await websocket.send_json({"type": "error", "message": "Payload must be a JSON object"})
                    continue
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid JSON format"})
                continue

            msg_type = data.get("type", "")

            if msg_type == "user_text":
                text = data.get("text", "")
                if not text:
                    await websocket.send_json({"type": "error", "message": "Empty text"})
                    continue

                async with async_session_factory() as turn_db:
                    res = await turn_db.execute(
                        select(Session).where(Session.id == session_id, Session.user_id == user.id)
                    )
                    curr_session = res.scalar_one_or_none()
                    if not curr_session:
                        await websocket.send_json({"type": "error", "message": "Session not found"})
                        break
                    response = await _process_turn(turn_db, session_id, user.id, text, curr_session)
                await websocket.send_json({"type": "assistant_response", **response})

            elif msg_type == "user_audio":
                import base64
                audio_b64 = data.get("audio", "")
                stt_mode = data.get("stt_mode", "web_speech")
                try:
                    audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
                except Exception:
                    await websocket.send_json({"type": "error", "message": "Invalid base64 audio data"})
                    continue

                if not audio_bytes:
                    await websocket.send_json({"type": "error", "message": "No audio data"})
                    continue

                await websocket.send_json({"type": "stt_processing", "status": "transcribing"})

                text = None
                if stt_mode == "personal_api":
                    stt = STTPersonalAPI()
                    text = await stt.transcribe(audio_bytes)
                elif stt_mode == "whisper_server":
                    stt = STTWhisperServer()
                    text = await stt.transcribe(audio_bytes)
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"STT mode '{stt_mode}' not supported via WebSocket. Use personal_api, whisper_server, or browser Web Speech.",
                    })
                    continue

                if not text:
                    await websocket.send_json({"type": "stt_failed", "message": "Could not transcribe audio"})
                    continue

                await websocket.send_json({"type": "stt_result", "text": text})

                try:
                    async with async_session_factory() as turn_db:
                        res = await turn_db.execute(
                            select(Session).where(Session.id == session_id, Session.user_id == user.id)
                        )
                        curr_session = res.scalar_one_or_none()
                        if not curr_session:
                            await websocket.send_json({"type": "error", "message": "Session not found"})
                            break
                        response = await _process_turn(turn_db, session_id, user.id, text, curr_session)
                        await turn_db.commit()
                    await websocket.send_json({"type": "assistant_response", **response})
                except Exception as e:
                    logger.error(f"Turn processing error in session {session_id}: {e}")
                    await websocket.send_json({"type": "error", "message": "Failed to process turn. Please try again."})

            elif msg_type == "end_session":
                from datetime import datetime, timezone
                async with async_session_factory() as turn_db:
                    res = await turn_db.execute(
                        select(Session).where(Session.id == session_id, Session.user_id == user.id)
                    )
                    session_obj = res.scalar_one_or_none()
                    if session_obj:
                        session_obj.ended_at = datetime.now(timezone.utc)
                        duration = 0.0
                        if session_obj.started_at:
                            ended = session_obj.ended_at if session_obj.ended_at.tzinfo else session_obj.ended_at.replace(tzinfo=timezone.utc)
                            started = session_obj.started_at if session_obj.started_at.tzinfo else session_obj.started_at.replace(tzinfo=timezone.utc)
                            duration = (ended - started).total_seconds() / 60.0
                        if duration > 0:
                            await bump_daily_minutes(turn_db, user.id, int(round(duration)))
                        await turn_db.commit()
                await websocket.send_json({"type": "session_ended"})
                break

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.exception(f"Unhandled WebSocket error in session {session_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "message": "An internal error occurred during the session turn"})
        except Exception:
            pass


async def _process_turn(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    text: str,
    session: Session,
) -> dict:
    history_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
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

    profile_dict = await get_tutor_profile_dict(db, user_id)

    system_prompt = build_system_prompt(
        scenario_key=scenario_key,
        scenario_custom_prompt=scenario_prompt,
        cefr_level=session.cefr_level,
        tutor_profile=profile_dict,
    )

    llm_router = LLMRouter(db, user_id)
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
    except Exception as e:
        logger.error(f"WebSocket LLM call failed for session {session_id}: {e}")

    reply = (parsed.get("reply") or "").strip()
    if not reply:
        reply = "Sorry, I lost my train of thought for a second — tell me a bit more about that."

    session.provider_used = provider_used

    # Now write user message and assistant reply in a short-lived transaction
    user_msg = Message(session_id=session_id, role="user", text=text)
    db.add(user_msg)
    await db.flush()

    assistant_msg = Message(
        session_id=session_id,
        role="assistant",
        text=reply,
    )
    db.add(assistant_msg)
    await db.flush()

    corrections_data = parsed.get("corrections", [])
    correction_list = []
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
        correction_list.append({
            "id": corr.id,
            "error_type": corr.error_type,
            "original": corr.original_fragment,
            "correction": corr.correction,
            "explanation": corr.explanation,
        })

    from app.models.models import VocabItem
    from app.srs.sm2 import next_review_date

    new_vocab_data = parsed.get("new_vocab", [])
    new_vocab_list = []
    for v in new_vocab_data:
        existing = await db.execute(
            select(VocabItem).where(
                VocabItem.user_id == user_id,
                VocabItem.word == v.get("word", ""),
            )
        )
        if existing.scalar_one_or_none():
            continue
        vocab = VocabItem(
            user_id=user_id,
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
        voice = await resolve_tts_voice(db, user_id, profile_dict)
        audio_bytes = await tts.synthesize(parsed.get("reply", ""), voice=voice)
        if audio_bytes:
            import base64
            audio_url = f"data:audio/mpeg;base64,{base64.b64encode(audio_bytes).decode()}"
            assistant_msg.audio_url = audio_url
    except Exception:
        pass

    await db.commit()

    return {
        "assistant_message": {
            "id": assistant_msg.id,
            "role": "assistant",
            "text": assistant_msg.text,
            "audio_url": audio_url,
        },
        "corrections": correction_list,
        "new_vocab": new_vocab_list,
        "audio_url": audio_url,
    }
