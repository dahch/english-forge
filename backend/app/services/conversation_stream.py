"""One conversation turn with incremental text delivery.

Transports (SSE today, WebSocket for direct clients) only differ in how they
put `emit()` on the wire; persistence, the correction pass and TTS live here.
The reply is streamed as plain text and a second, non-streamed call extracts
corrections/vocabulary — streaming the structured JSON would show field names
to the student.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.concurrency import session_turn_lock
from app.llm.prompts import (
    build_correction_analysis_prompt,
    build_streaming_system_prompt,
)
from app.llm.router import LLMRouter, parse_llm_json
from app.models.models import Message, Scenario, Session, User
from app.utils import get_tutor_profile_dict

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

_EMPTY_REVIEW: dict[str, Any] = {"corrections": [], "new_vocab": []}


async def _analyze_turn(
    db: AsyncSession, user: User, text: str, cefr_level: str
) -> dict[str, Any]:
    """Corrections + new vocabulary for the student's message (best effort)."""
    router = LLMRouter(db, user.id)
    prompt = build_correction_analysis_prompt(text, cefr_level)
    for attempt in range(2):
        try:
            result = await router.complete_with_fallback(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="You analyze English learner messages and return valid JSON only.",
                task="correction",
                temperature=0.2,
                max_tokens=900,
            )
            parsed = parse_llm_json(result.get("content", ""))
            corrections = [
                item for item in (parsed.get("corrections") or []) if isinstance(item, dict)
            ]
            new_vocab = [
                item for item in (parsed.get("new_vocab") or []) if isinstance(item, dict)
            ]
            return {"corrections": corrections, "new_vocab": new_vocab}
        except Exception as exc:  # noqa: BLE001 - review is best-effort
            logger.warning(
                "Correction analysis attempt %s/2 failed: %s", attempt + 1, exc
            )
    return dict(_EMPTY_REVIEW)


async def stream_turn(
    db: AsyncSession,
    current_user: User,
    session: Session,
    text: str,
    emit: Emit,
) -> None:
    """Stream one tutor reply and persist the complete turn.

    Emits: `assistant_started` (provider/model), `assistant_delta` (text), and
    `assistant_response` with the persisted turn. Exceptions propagate so the
    transport can emit its own error event.
    """
    # Imported lazily: the REST router imports this module for its SSE endpoint,
    # so a top-level import would be circular.
    from app.routers.messages import _attach_audio, _message_response, _persist_turn

    async with session_turn_lock(session.id):
        history_result = await db.execute(
            select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
        )
        history = history_result.scalars().all()
        messages_for_llm = [{"role": m.role, "content": m.text} for m in history]
        messages_for_llm.append({"role": "user", "content": text})

        scenario_prompt = None
        scenario_key = None
        if session.scenario_id:
            scenario_result = await db.execute(
                select(Scenario).where(Scenario.id == session.scenario_id)
            )
            scenario = scenario_result.scalar_one_or_none()
            if scenario:
                if scenario.is_custom:
                    scenario_prompt = scenario.system_prompt
                else:
                    scenario_key = scenario.name.lower().replace(" ", "_").replace("-", "_")

        profile_dict = await get_tutor_profile_dict(db, current_user.id)
        system_prompt = build_streaming_system_prompt(
            scenario_key=scenario_key,
            scenario_custom_prompt=scenario_prompt,
            cefr_level=session.cefr_level,
            tutor_profile=profile_dict,
        )

        router = LLMRouter(db, current_user.id)
        chunks: list[str] = []
        provider = "unknown"
        async for chunk in router.stream_with_fallback(
            messages=messages_for_llm,
            system_prompt=system_prompt,
            task="conversation",
            temperature=0.7,
            max_tokens=900,
        ):
            if not chunks:
                provider = router.last_provider or "unknown"
                await emit(
                    "assistant_started",
                    {"provider": provider, "model": router.last_model},
                )
            chunks.append(chunk)
            await emit("assistant_delta", {"text": chunk})

        reply = "".join(chunks).strip()
        if not reply:
            raise RuntimeError("The tutor returned an empty reply")

        # Best-effort review; a failure must not lose the reply.
        parsed = await _analyze_turn(db, current_user, text, session.cefr_level)

        (
            user_msg,
            assistant_msg,
            correction_responses,
            new_vocab_list,
        ) = await _persist_turn(db, current_user, session, text, reply, parsed, provider)
        await db.commit()

        await _attach_audio(db, current_user, assistant_msg, profile_dict)

        # Reuse the REST response contract so both transports send identical
        # payloads (including has_audio, never the storage reference).
        from app.schemas.session import ConversationTurnResponse

        turn = ConversationTurnResponse(
            user_message=_message_response(user_msg, correction_responses),
            assistant_message=_message_response(assistant_msg, correction_responses),
            corrections=correction_responses,
            new_vocab=new_vocab_list,
            audio_url=None,
        )
        await emit("assistant_response", turn.model_dump(mode="json"))
