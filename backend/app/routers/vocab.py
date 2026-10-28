from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated
import random
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import ProgressDaily, User, VocabItem
from app.schemas.vocab import (
    QuizQuestion,
    QuizResult,
    QuizSubmission,
    ReviewCardResponse,
    ReviewSubmission,
    VocabItemCreate,
    VocabItemResponse,
    VocabItemUpdate,
)
from app.srs.sm2 import sm2_update, next_review_date
from app.utils import bump_daily_words

router = APIRouter(prefix="/api/vocab", tags=["vocab"])


@router.get("", response_model=list[VocabItemResponse])
async def list_vocab(
    filter: str = Query("all", pattern="^(all|due|new)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    # Annotated defaults are real ints, so direct handler calls in tests/scripts
    # keep working (a Query(...) default would leak into them).
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    query = select(VocabItem).where(VocabItem.user_id == current_user.id)
    if filter == "due":
        query = query.where(
            or_(VocabItem.next_review_at <= date.today(), VocabItem.next_review_at.is_(None))
        )
    elif filter == "new":
        query = query.where(VocabItem.interval_days == 0)
    query = query.order_by(VocabItem.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    items = result.scalars().all()
    return [VocabItemResponse.model_validate(v) for v in items]


@router.post("", response_model=VocabItemResponse, status_code=201)
async def create_vocab(
    body: VocabItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = VocabItem(
        user_id=current_user.id,
        word=body.word,
        definition=body.definition,
        example=body.example,
        ipa=body.ipa,
        ease_factor=2.5,
        interval_days=0,
        next_review_at=next_review_date(0),
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        # Unique index on (user_id, lower(word)): report it instead of a 500.
        await db.rollback()
        raise HTTPException(status_code=409, detail="This word is already in your vocabulary")
    await bump_daily_words(db, current_user.id, 1)
    await db.refresh(item)
    return VocabItemResponse.model_validate(item)


@router.put("/{vocab_id}", response_model=VocabItemResponse)
async def update_vocab(
    vocab_id: str,
    body: VocabItemUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem).where(VocabItem.id == vocab_id, VocabItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Vocab item not found")

    if body.word is not None:
        item.word = body.word
    if body.definition is not None:
        item.definition = body.definition
    if body.example is not None:
        item.example = body.example
    if body.ipa is not None:
        item.ipa = body.ipa

    await db.flush()
    await db.refresh(item)
    return VocabItemResponse.model_validate(item)


@router.delete("/{vocab_id}", status_code=204)
async def delete_vocab(
    vocab_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem).where(VocabItem.id == vocab_id, VocabItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Vocab item not found")
    await db.delete(item)


@router.post("/{vocab_id}/review", response_model=VocabItemResponse)
async def review_vocab(
    vocab_id: str,
    body: ReviewSubmission,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem).where(VocabItem.id == vocab_id, VocabItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Vocab item not found")

    new_ef, new_interval = sm2_update(body.quality, item.ease_factor, item.interval_days)
    item.ease_factor = new_ef
    item.interval_days = new_interval
    item.next_review_at = next_review_date(new_interval)
    item.last_reviewed_at = datetime.now(timezone.utc)

    today = date.today()
    prog_result = await db.execute(
        select(ProgressDaily).where(
            ProgressDaily.user_id == current_user.id,
            ProgressDaily.date == today,
        )
    )
    progress = prog_result.scalar_one_or_none()
    if progress:
        progress.reviews_done += 1
    else:
        progress = ProgressDaily(
            user_id=current_user.id,
            date=today,
            reviews_done=1,
        )
        db.add(progress)

    await db.flush()
    await db.refresh(item)
    return VocabItemResponse.model_validate(item)


@router.get("/review/due", response_model=list[ReviewCardResponse])
async def get_due_cards(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem)
        .where(
            VocabItem.user_id == current_user.id,
            or_(VocabItem.next_review_at <= date.today(), VocabItem.next_review_at.is_(None)),
        )
        .order_by(VocabItem.next_review_at.asc().nulls_first())
        .limit(limit)
    )
    items = result.scalars().all()
    return [ReviewCardResponse.model_validate(v) for v in items]


@router.post("/quiz/generate", response_model=list[QuizQuestion])
async def generate_quiz(
    count: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem)
        .where(VocabItem.user_id == current_user.id)
        .order_by(func.random())
        .limit(count * 3)
    )
    all_items = result.scalars().all()
    if len(all_items) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 vocab items for quiz")

    questions: list[QuizQuestion] = []
    for item in all_items[:count]:
        q_type = random.choice(["multiple_choice", "fill_blank"])

        if q_type == "multiple_choice":
            wrong = [v for v in all_items if v.id != item.id]
            random.shuffle(wrong)
            options = [item.definition] + [w.definition for w in wrong[:3]]
            random.shuffle(options)
            questions.append(QuizQuestion(
                vocab_item_id=item.id,
                question_type="multiple_choice",
                question=f"What is the meaning of \"{item.word}\"?",
                options=options,
                correct_answer=item.definition,
            ))
        else:
            if item.example:
                blanked = re.sub(rf"\b{re.escape(item.word)}\b", "______", item.example, flags=re.IGNORECASE, count=1)
                questions.append(QuizQuestion(
                    vocab_item_id=item.id,
                    question_type="fill_blank",
                    question=f"Fill in the blank: {blanked}",
                    correct_answer=item.word,
                ))
            else:
                wrong = [v for v in all_items if v.id != item.id]
                random.shuffle(wrong)
                options = [item.word] + [w.word for w in wrong[:3]]
                random.shuffle(options)
                questions.append(QuizQuestion(
                    vocab_item_id=item.id,
                    question_type="multiple_choice",
                    question=f"Which word matches: \"{item.definition}\"?",
                    options=options,
                    correct_answer=item.word,
                ))

    return questions


@router.post("/quiz/check", response_model=QuizResult)
async def check_quiz_answer(
    body: QuizSubmission,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VocabItem).where(VocabItem.id == body.vocab_item_id, VocabItem.user_id == current_user.id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Vocab item not found")

    is_correct = body.answer.strip().lower() == item.word.lower() or body.answer.strip().lower() == item.definition.lower()
    return QuizResult(
        correct=is_correct,
        correct_answer=item.word,
        explanation=f"The correct answer is \"{item.word}\" — {item.definition}" if not is_correct else "Correct!",
    )
