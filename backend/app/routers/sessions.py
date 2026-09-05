from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Scenario, Session, Message, User
from app.schemas.session import (
    SessionCreate,
    SessionResponse,
    SessionSummaryResponse,
    CorrectionResponse,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scenario_name = None
    if body.scenario_id:
        result = await db.execute(select(Scenario).where(Scenario.id == body.scenario_id))
        scenario = result.scalar_one_or_none()
        if scenario:
            scenario_name = scenario.name

    session = Session(
        user_id=current_user.id,
        scenario_id=body.scenario_id,
        cefr_level=body.cefr_level,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    resp = SessionResponse(
        id=session.id,
        scenario_id=session.scenario_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        cefr_level=session.cefr_level,
        provider_used=session.provider_used,
        scenario_name=scenario_name,
        message_count=0,
    )
    return resp


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id)
        .order_by(Session.started_at.desc())
        .offset(skip)
        .limit(limit)
    )
    sessions = result.scalars().all()

    responses = []
    for s in sessions:
        msg_count = await db.execute(
            select(func.count(Message.id)).where(Message.session_id == s.id)
        )
        count = msg_count.scalar() or 0

        scenario_name = None
        if s.scenario_id:
            sc = await db.execute(select(Scenario.name).where(Scenario.id == s.scenario_id))
            scenario_name = sc.scalar_one_or_none()

        responses.append(SessionResponse(
            id=s.id,
            scenario_id=s.scenario_id,
            started_at=s.started_at,
            ended_at=s.ended_at,
            cefr_level=s.cefr_level,
            provider_used=s.provider_used,
            scenario_name=scenario_name,
            message_count=count,
        ))
    return responses


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
    from datetime import datetime, timezone

    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == current_user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.ended_at = datetime.now(timezone.utc)

    msg_result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    from app.models.models import Correction
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

    from app.models.models import VocabItem
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

    duration = 0.0
    if session.ended_at and session.started_at:
        duration = (session.ended_at - session.started_at).total_seconds() / 60.0

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
