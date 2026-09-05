from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Correction, Message, ProgressDaily, Session, User, VocabItem

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total_sessions = await db.execute(
        select(func.count(Session.id)).where(Session.user_id == current_user.id)
    )
    total_sessions_count = total_sessions.scalar() or 0

    total_minutes = await db.execute(
        select(func.sum(ProgressDaily.minutes_spoken)).where(ProgressDaily.user_id == current_user.id)
    )
    total_minutes_val = total_minutes.scalar() or 0

    total_words = await db.execute(
        select(func.count(VocabItem.id)).where(VocabItem.user_id == current_user.id)
    )
    total_words_count = total_words.scalar() or 0

    total_reviews = await db.execute(
        select(func.sum(ProgressDaily.reviews_done)).where(ProgressDaily.user_id == current_user.id)
    )
    total_reviews_count = total_reviews.scalar() or 0

    today = date.today()
    streak = 0
    for i in range(365):
        check_date = today - timedelta(days=i)
        result = await db.execute(
            select(ProgressDaily).where(
                ProgressDaily.user_id == current_user.id,
                ProgressDaily.date == check_date,
            )
        )
        day = result.scalar_one_or_none()
        if day and (day.minutes_spoken > 0 or day.reviews_done > 0):
            streak += 1
        elif i > 0:
            break

    due_today = await db.execute(
        select(func.count(VocabItem.id)).where(
            VocabItem.user_id == current_user.id,
            VocabItem.next_review_at <= today,
        )
    )
    due_count = due_today.scalar() or 0

    return {
        "total_sessions": total_sessions_count,
        "total_minutes": total_minutes_val,
        "total_words_learned": total_words_count,
        "total_reviews": total_reviews_count,
        "current_streak": streak,
        "due_for_review": due_count,
    }


@router.get("/weekly")
async def get_weekly(
    weeks: int = 4,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    start = today - timedelta(weeks=weeks)

    result = await db.execute(
        select(ProgressDaily)
        .where(
            ProgressDaily.user_id == current_user.id,
            ProgressDaily.date >= start,
        )
        .order_by(ProgressDaily.date)
    )
    entries = result.scalars().all()

    by_week: dict[str, dict] = {}
    for entry in entries:
        week_start = entry.date - timedelta(days=entry.date.weekday())
        key = week_start.isoformat()
        if key not in by_week:
            by_week[key] = {"week": key, "minutes": 0, "new_words": 0, "reviews": 0}
        by_week[key]["minutes"] += entry.minutes_spoken
        by_week[key]["new_words"] += entry.new_words
        by_week[key]["reviews"] += entry.reviews_done

    return list(by_week.values())


@router.get("/cefr")
async def estimate_cefr(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Heuristic CEFR estimation based on:
    - Average sentence length (longer = higher level)
    - Lexical diversity (type/token ratio — more unique words = higher)
    - Error rate per 100 words (fewer errors = higher)
    - Vocabulary sophistication (ratio of B2+ words, approximated by vocab_items count)

    This is a rough heuristic, not a scientifically validated assessment.
    Each metric is normalized to 0-1 and weighted:
      avg_sentence_length: 0.25
      lexical_diversity: 0.30
      accuracy (1 - error_rate): 0.30
      vocab_sophistication: 0.15
    """
    msg_result = await db.execute(
        select(Message.text).where(
            Message.role == "user",
            Message.session_id.in_(
                select(Session.id).where(Session.user_id == current_user.id)
            ),
        )
    )
    user_texts = [row[0] for row in msg_result.all()]

    if not user_texts:
        return {"estimated_level": "A1", "confidence": 0.0, "metrics": {}}

    all_text = " ".join(user_texts)
    words = all_text.split()
    total_words = len(words)
    if total_words == 0:
        return {"estimated_level": "A1", "confidence": 0.0, "metrics": {}}

    sentences = [s.strip() for s in all_text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    avg_sentence_len = total_words / max(len(sentences), 1)
    sentence_score = min(avg_sentence_len / 25.0, 1.0)

    unique_words = set(w.lower() for w in words)
    lexical_diversity = len(unique_words) / total_words
    diversity_score = min(lexical_diversity / 0.8, 1.0)

    corr_result = await db.execute(
        select(func.count(Correction.id))
        .join(Message, Correction.message_id == Message.id)
        .where(
            Message.session_id.in_(
                select(Session.id).where(Session.user_id == current_user.id)
            ),
            Message.role == "user",
        )
    )
    total_corrections = corr_result.scalar() or 0
    error_rate = total_corrections / max(total_words / 100.0, 1.0)
    accuracy_score = max(1.0 - (error_rate / 10.0), 0.0)

    vocab_count = await db.execute(
        select(func.count(VocabItem.id)).where(VocabItem.user_id == current_user.id)
    )
    vocab_total = vocab_count.scalar() or 0
    vocab_score = min(vocab_total / 200.0, 1.0)

    final_score = (
        sentence_score * 0.25
        + diversity_score * 0.30
        + accuracy_score * 0.30
        + vocab_score * 0.15
    )

    if final_score >= 0.85:
        level = "C2"
    elif final_score >= 0.70:
        level = "C1"
    elif final_score >= 0.55:
        level = "B2"
    elif final_score >= 0.40:
        level = "B1"
    elif final_score >= 0.25:
        level = "A2"
    else:
        level = "A1"

    return {
        "estimated_level": level,
        "confidence": round(final_score, 3),
        "metrics": {
            "avg_sentence_length": round(avg_sentence_len, 1),
            "lexical_diversity": round(lexical_diversity, 3),
            "error_rate_per_100_words": round(error_rate, 2),
            "total_words": total_words,
            "total_corrections": total_corrections,
            "vocab_size": vocab_total,
        },
    }
