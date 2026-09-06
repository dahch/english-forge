"""Assessment phase state machine.

Pure domain logic, deliberately outside the FastAPI router: the router only
adapts HTTP ↔ this service. Handles the mic_check → conversation → listening →
speaking flow, per-item grading, deterministic pronunciation scoring and the
final analysis.

Evidence integrity rules (review findings):
- Pronunciation metrics are ALWAYS recomputed server-side from
  (expected item text, transcript, STT word timestamps). The client only
  echoes the STT words — it can never inject a score.
- Each banked item carries an item_id; progression and the analysis dedupe by
  item_id, so double submissions cannot double-advance or double-count
  evidence.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.services.pronunciation import normalize_text, score_pronunciation_async
from app.utils import get_tutor_profile_dict, resolve_tts_voice

logger = logging.getLogger(__name__)

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

_CEFR_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}

# Reanalyze cost guard: cooldown between LLM analyses of the same assessment.
_REANALYZE_COOLDOWN = timedelta(seconds=30)

# End requests are only honored in short, standalone utterances ("I'm done",
# "let's finish"). A bare substring match would force-finish the session when
# the student merely mentions these words ("I'm done with work for today").
_END_WORDS_RE = re.compile(r"\b(finish|end|stop|terminar|basta|done)\b", re.IGNORECASE)


def wants_to_finish(text: str) -> bool:
    stripped = text.strip().strip(".!?,;:¡¿")
    return len(stripped.split()) <= 4 and bool(_END_WORDS_RE.search(stripped))


def clamp_score(value, default: float | None = None) -> float | None:
    """Coerce an LLM-produced dimension score to a clamped float."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, score))


# --- ORM helpers -------------------------------------------------------------

async def reload_assessment(db: AsyncSession, assessment_id: str) -> Assessment:
    """Re-load the assessment with its messages.

    populate_existing forces the messages collection to re-load even when the
    Assessment object is already in the identity map: phase transitions add
    messages earlier in the same request (farewell + next item), and without
    this the response would silently omit them.
    """
    result = await db.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(selectinload(Assessment.messages))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


def _assistant_message(assessment_id: str, text: str, kind: str, metrics: dict | None = None) -> AssessmentMessage:
    msg = AssessmentMessage(assessment_id=assessment_id, role="assistant", text=text, kind=kind)
    if metrics:
        msg.metrics = json.dumps(metrics, ensure_ascii=False)
    return msg


def _user_message(
    assessment_id: str, text: str, kind: str, source: str, metrics: dict | None = None
) -> AssessmentMessage:
    msg = AssessmentMessage(assessment_id=assessment_id, role="user", text=text, kind=kind)
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


def expected_text_for(phase: str, step: int) -> str | None:
    if phase == PHASE_MIC_CHECK:
        return _MIC_CHECK_EXPECTED
    if phase == PHASE_SPEAKING:
        item = _item_for_step(phase, step)
        return item.text if item else None
    return None


async def _answered_item_ids(db: AsyncSession, assessment_id: str, kind: str) -> set[str]:
    """Distinct item ids the student has already answered in this section."""
    result = await db.execute(
        select(AssessmentMessage.metrics).where(
            AssessmentMessage.assessment_id == assessment_id,
            AssessmentMessage.role == "user",
            AssessmentMessage.kind == kind,
        )
    )
    ids: set[str] = set()
    for (metrics_raw,) in result.all():
        try:
            item_id = (json.loads(metrics_raw or "{}") or {}).get("item_id")
        except Exception:
            item_id = None
        if item_id:
            ids.add(item_id)
    return ids


def _conversation_questions_asked(messages: list[AssessmentMessage]) -> int:
    return sum(1 for m in messages if m.role == "assistant" and m.kind == KIND_CHAT)


# --- Conversation ------------------------------------------------------------

async def _build_next_question(
    db: AsyncSession, current_user: User, assessment: Assessment, messages: list[AssessmentMessage]
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


async def _handle_mic_check(
    db: AsyncSession, current_user: User, assessment: Assessment, text: str, source: str
) -> tuple[Assessment, bool]:
    db.add(_user_message(assessment.id, text, KIND_MIC_CHECK, source))

    # Move to the interview and ask question 1 (LLM greets + asks).
    assessment.phase = PHASE_CONVERSATION
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    parsed = await _build_next_question(db, current_user, assessment, list(assessment.messages))
    reply = parsed.get("reply", "") if parsed else (
        "Great, your microphone works! So, let's start easy: what do you like to do in your free time?"
    )
    db.add(_assistant_message(assessment.id, reply, KIND_CHAT))
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    return assessment, False


async def _handle_conversation(
    db: AsyncSession, current_user: User, assessment: Assessment, text: str, source: str
) -> tuple[Assessment, bool]:
    db.add(_user_message(assessment.id, text, KIND_CHAT, source))
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    messages = list(assessment.messages)
    questions_asked = _conversation_questions_asked(messages)

    # Server-side completion triggers: the 10-question cap, or the student
    # explicitly asking to finish. The LLM can only end the section on its own
    # once MIN_ASSESSMENT_EXCHANGES have happened — a 2-3 answer interview is
    # not enough evidence to place a level.
    server_complete = (
        questions_asked >= MAX_ASSESSMENT_EXCHANGES or wants_to_finish(text)
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
        db.add(_assistant_message(
            assessment.id, first_item.tutor_text, KIND_LISTENING,
            metrics={"item_id": first_item.id},
        ))
        await db.flush()
        assessment = await reload_assessment(db, assessment.id)
        return assessment, False

    reply = parsed.get("reply", "") if parsed else ""
    db.add(_assistant_message(assessment.id, reply, KIND_CHAT))
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    return assessment, False


# --- Listening ---------------------------------------------------------------

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
                # Reasoning models need headroom for thinking before the JSON
                # answer — a tiny budget returns empty content.
                max_tokens=1000,
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


async def _handle_listening(
    db: AsyncSession, current_user: User, assessment: Assessment, text: str, source: str, item_id: str | None
) -> tuple[Assessment, bool]:
    item = _item_for_step(PHASE_LISTENING, assessment.section_step)
    if item is None:
        # Bank exhausted but the phase never advanced (edge case) — advance now.
        return await _advance_to_speaking(db, current_user, assessment)

    # The client declares which item it is answering; a stale/duplicate
    # submission (item no longer current) is dropped silently. NOTE: this
    # check is not atomic — two concurrent /message calls for the same item
    # could both pass before either inserts. Low risk in a single-user app
    # (the real double-tap is sequential and caught here); a truly atomic
    # guard would need a unique index on (assessment_id, item_id), which
    # requires promoting item_id out of the metrics JSON column.
    if item_id is not None and item_id != item.id:
        assessment = await reload_assessment(db, assessment.id)
        return assessment, False

    answered_before = await _answered_item_ids(db, assessment.id, KIND_LISTENING)
    if item.id in answered_before:
        # Duplicate submission for an already-answered item (double-click) —
        # don't insert, don't advance; return the current state.
        assessment = await reload_assessment(db, assessment.id)
        return assessment, False

    grading = await _grade_listening_answer(db, current_user, item, text)
    db.add(_user_message(
        assessment.id, text, KIND_LISTENING, source,
        metrics={"item_id": item.id, **grading},
    ))
    await db.flush()

    answered = answered_before | {item.id}
    if len(answered) >= len(LISTENING_ITEMS):
        return await _advance_to_speaking(db, current_user, assessment)

    # Consecutive fails (from the end of the history backwards) trigger the
    # adaptive early stop. Query fresh — the caller's collection is stale.
    recent = await db.execute(
        select(AssessmentMessage)
        .where(
            AssessmentMessage.assessment_id == assessment.id,
            AssessmentMessage.role == "user",
            AssessmentMessage.kind == KIND_LISTENING,
        )
        .order_by(AssessmentMessage.created_at.desc(), AssessmentMessage.id.desc())
    )
    consecutive_fails = 0
    for m in recent.scalars():
        try:
            correct = (json.loads(m.metrics or "{}") or {}).get("correct")
        except Exception:
            correct = None
        if correct == 1:
            break
        consecutive_fails += 1
    if consecutive_fails >= LISTENING_EARLY_STOP_FAILS:
        return await _advance_to_speaking(db, current_user, assessment)

    assessment.section_step = len(answered)
    await db.flush()
    next_item = LISTENING_ITEMS[assessment.section_step]
    db.add(_assistant_message(
        assessment.id, next_item.tutor_text, KIND_LISTENING,
        metrics={"item_id": next_item.id},
    ))
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    return assessment, False


async def _advance_to_speaking(
    db: AsyncSession, current_user: User, assessment: Assessment
) -> tuple[Assessment, bool]:
    assessment.phase = PHASE_SPEAKING
    assessment.section_step = 0
    await db.flush()
    first_item = SPEAKING_ITEMS[0]
    db.add(_assistant_message(
        assessment.id,
        f"{first_item.text}\n\nFocus: {first_item.focus}",
        KIND_SPEAKING,
        metrics={"item_id": first_item.id},
    ))
    await db.flush()
    assessment = await reload_assessment(db, assessment.id)
    return assessment, False


# --- Speaking ----------------------------------------------------------------

async def _handle_speaking(
    db: AsyncSession,
    current_user: User,
    assessment: Assessment,
    text: str,
    source: str,
    words: list[dict] | None,
    item_id: str | None,
) -> tuple[Assessment, bool]:
    item = _item_for_step(PHASE_SPEAKING, assessment.section_step)
    if item is None:
        raise ValueError("Speaking section already finished")

    # Stale/duplicate submission (item no longer current) — dropped silently.
    if item_id is not None and item_id != item.id:
        assessment = await reload_assessment(db, assessment.id)
        return assessment, False

    answered_before = await _answered_item_ids(db, assessment.id, KIND_SPEAKING)
    if item.id in answered_before:
        # Duplicate submission — return current state untouched.
        assessment = await reload_assessment(db, assessment.id)
        return assessment, False

    # Evidence integrity: ALWAYS recompute server-side from (item text,
    # transcript, STT word timestamps). The client only echoes the words.
    metrics = await score_pronunciation_async(item.text, text, words or None)
    db.add(_user_message(
        assessment.id, text, KIND_SPEAKING, source,
        metrics={"item_id": item.id, **metrics},
    ))
    await db.flush()

    answered = answered_before | {item.id}
    if len(answered) >= len(SPEAKING_ITEMS):
        # Every section done — the client should now call /complete.
        db.add(_assistant_message(
            assessment.id,
            "Perfect, that's everything! Let me put your results together.",
            KIND_CHAT,
        ))
        await db.flush()
        assessment = await reload_assessment(db, assessment.id)
        return assessment, True

    assessment.section_step = len(answered)
    await db.flush()
    next_item = SPEAKING_ITEMS[assessment.section_step]
    db.add(_assistant_message(
        assessment.id,
        f"{next_item.text}\n\nFocus: {next_item.focus}",
        KIND_SPEAKING,
        metrics={"item_id": next_item.id},
    ))
    await db.flush()

    assessment = await reload_assessment(db, assessment.id)
    return assessment, False


# --- Entry point -------------------------------------------------------------

async def handle_start(db: AsyncSession, assessment: Assessment) -> Assessment:
    """Seed a freshly-created (empty) assessment with the deterministic mic
    check and return it eager-loaded. The caller is responsible for creating
    the Assessment row and guarding against duplicates (partial unique index)."""
    db.add(_assistant_message(assessment.id, _MIC_CHECK_TEXT, KIND_MIC_CHECK))
    await db.flush()
    return await reload_assessment(db, assessment.id)


async def handle_message(
    db: AsyncSession,
    current_user: User,
    assessment: Assessment,
    *,
    text: str,
    source: str,
    words: list[dict] | None = None,
    item_id: str | None = None,
) -> tuple[Assessment, bool]:
    """Dispatch a student message to the active phase's handler.

    `item_id` is the banked item the client believes it is answering — stale
    or duplicate submissions are dropped without advancing the section.
    Returns (assessment, is_complete) — is_complete means every section is
    done and the client should call /complete.
    """
    phase = assessment.phase or PHASE_CONVERSATION
    if phase == PHASE_MIC_CHECK:
        return await _handle_mic_check(db, current_user, assessment, text, source)
    if phase == PHASE_CONVERSATION:
        return await _handle_conversation(db, current_user, assessment, text, source)
    if phase == PHASE_LISTENING:
        return await _handle_listening(db, current_user, assessment, text, source, item_id)
    if phase == PHASE_SPEAKING:
        return await _handle_speaking(db, current_user, assessment, text, source, words, item_id)
    raise ValueError(f"Unknown assessment phase: {phase}")


# --- TTS caching -------------------------------------------------------------

async def ensure_message_audio(
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


# --- Analysis ----------------------------------------------------------------

def _fallback_analysis(
    listening_dim: float | None,
    pronunciation_dim: float | None,
    n_listening: int,
    n_speaking: int,
) -> dict:
    """Deterministic analysis used when the LLM is unavailable: the assessment
    still completes with the level computed from the measured evidence instead
    of failing the /complete request. "Re-analyze Results" retries the LLM."""
    measured: list[str] = []
    if listening_dim is not None:
        measured.append(f"comprensión auditiva {listening_dim:.0f}/100 ({n_listening} preguntas)")
    if pronunciation_dim is not None:
        measured.append(f"pronunciación {pronunciation_dim:.0f}/100 ({n_speaking} grabaciones)")
    evidence = " y ".join(measured) if measured else "no hubo evidencia objetiva suficiente"
    return {
        "grammar": None,
        "vocabulary": None,
        "fluency": None,
        "recommendations": [
            "Vuelve a intentar el análisis más tarde desde «Re-analyze Results»."
        ],
        "summary": (
            "No se pudo generar el análisis detallado en este momento, pero tu nivel se calculó "
            f"con la evidencia medida: {evidence}. "
            "Puedes pulsar «Re-analyze Results» para repetir el análisis más tarde."
        ),
    }


async def analyze_assessment(db: AsyncSession, current_user: User, assessment: Assessment) -> None:
    """Run the assessment analysis and persist the results (level, confidence,
    dimension scores, strengths, weaknesses, recommendations, summary).

    Dimension scores for listening/pronunciation are computed deterministically
    from graded items / pronunciation metrics; the LLM scores the conversation
    dimensions with a rubric and writes the qualitative fields. The final
    level and confidence are aggregated deterministically — the LLM is never
    allowed to invent a level or claim unaudited dimensions.
    """
    result = await db.execute(
        select(AssessmentMessage)
        .where(AssessmentMessage.assessment_id == assessment.id)
        .order_by(AssessmentMessage.created_at, AssessmentMessage.id)
    )
    messages = list(result.scalars().all())

    if len(messages) < 2:
        raise ValueError("Assessment has too few messages to evaluate")

    # --- Deterministic dimensions from stored evidence (deduped by item_id) ---
    def _user_metrics(kind: str) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []
        for m in messages:
            if m.role != "user" or m.kind != kind or not m.metrics:
                continue
            try:
                payload = json.loads(m.metrics)
            except Exception:
                continue
            item_id = payload.get("item_id") or m.id
            if item_id in seen:
                continue
            seen.add(item_id)
            out.append(payload)
        return out

    listening_correct: list[float] = [
        100.0 if p.get("correct") == 1 else 0.0 for p in _user_metrics(KIND_LISTENING)
    ]
    listening_dim = mean_or_none(listening_correct)

    pron_scores: list[float] = []
    for payload in _user_metrics(KIND_SPEAKING):
        score = clamp_score(payload.get("composite"))
        if score is not None:
            pron_scores.append(score)
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
    parsed: dict | None = None
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
                # Reasoning models burn tokens thinking before writing the
                # JSON payload — 2048 left no room for the answer.
                max_tokens=4096,
            )
        except Exception as e:
            logger.error(f"Assessment analysis LLM call attempt {attempt + 1}/3 failed for assessment {assessment.id}: {e}")
            continue

        raw_analysis = analysis_result.get("content", "")
        logger.info(
            f"Assessment analysis raw response for assessment {assessment.id} "
            f"(provider={analysis_result.get('provider', 'unknown')}, model={analysis_result.get('model', 'unknown')}, "
            f"attempt={attempt + 1}/3, length={len(raw_analysis)}): {raw_analysis[:1500]}"
        )
        if not raw_analysis.strip():
            logger.warning(f"Assessment analysis LLM returned empty content (attempt {attempt + 1}/3)")
            continue

        try:
            parsed = parse_llm_json(raw_analysis)
        except Exception as e:
            logger.error(f"Assessment analysis JSON parsing failed for assessment {assessment.id}: {e}")
            continue

        # The parsed shape must actually look like an analysis — the legacy
        # conversational fallback ({"reply": ...}) would otherwise silently save
        # empty result cards (blank summary/strengths/weaknesses).
        if parsed.get("summary"):
            break
        logger.error(
            f"Assessment analysis for assessment {assessment.id} returned an unexpected shape: "
            f"keys={list(parsed.keys())}, raw={raw_analysis[:500]}"
        )
        parsed = None

    if parsed is None or not parsed.get("summary"):
        # LLM unavailable or unusable after all attempts — complete with a
        # deterministic analysis from the measured evidence instead of failing
        # the /complete request with a 503 (the client would then retry in a
        # loop, exhausting proxy connections).
        logger.warning(f"Assessment {assessment.id}: LLM analysis unavailable, using deterministic fallback")
        parsed = _fallback_analysis(listening_dim, pronunciation_dim, len(listening_correct), len(pron_scores))

    # --- Aggregation (deterministic) ---
    dimension_scores: dict[str, float | None] = {
        "grammar": clamp_score(parsed.get("grammar")),
        "vocabulary": clamp_score(parsed.get("vocabulary")),
        "fluency": clamp_score(parsed.get("fluency")),
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
    assessment.strengths = json.dumps(strengths, ensure_ascii=False)
    assessment.weaknesses = json.dumps(weaknesses, ensure_ascii=False)
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


def _coerce_json_list(value) -> str:
    """Serialize a value as a JSON list, unwrapping double-encoded strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    if not isinstance(value, list):
        value = [value] if value else []
    return json.dumps([str(item) for item in value])
