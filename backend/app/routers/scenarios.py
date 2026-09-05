from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Scenario, User
from app.schemas.session import ScenarioCreate, ScenarioResponse
from app.llm.prompts import SCENARIO_PROMPTS

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

PREDEFINED_SCENARIOS = [
    {"name": "Job Interview", "key": "job_interview", "cefr": "B1"},
    {"name": "Ordering Food", "key": "ordering_food", "cefr": "A2"},
    {"name": "Hotel Check-in", "key": "hotel_checkin", "cefr": "A2"},
    {"name": "Small Talk", "key": "small_talk", "cefr": "B1"},
    {"name": "Business Meeting", "key": "business_meeting", "cefr": "B2"},
    {"name": "Phone Call", "key": "phone_call", "cefr": "B1"},
    {"name": "Free Talk", "key": "free_talk", "cefr": "B1"},
]


async def _seed_scenarios(db: AsyncSession):
    result = await db.execute(select(Scenario).where(Scenario.user_id.is_(None), Scenario.is_custom == False))
    existing = result.scalars().all()
    if existing:
        return

    for s in PREDEFINED_SCENARIOS:
        scenario = Scenario(
            name=s["name"],
            system_prompt=SCENARIO_PROMPTS.get(s["key"], ""),
            cefr_level=s["cefr"],
            is_custom=False,
            user_id=None,
        )
        db.add(scenario)
    await db.flush()


@router.get("", response_model=list[ScenarioResponse])
async def list_scenarios(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _seed_scenarios(db)

    result = await db.execute(
        select(Scenario)
        .where((Scenario.user_id.is_(None)) | (Scenario.user_id == current_user.id))
        .order_by(Scenario.is_custom, Scenario.name)
    )
    scenarios = result.scalars().all()
    return [ScenarioResponse.model_validate(s) for s in scenarios]


@router.post("", response_model=ScenarioResponse, status_code=201)
async def create_scenario(
    body: ScenarioCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.llm.router import LLMRouter
    from app.llm.prompts import build_system_prompt

    gen_prompt = (
        f"You are a roleplay partner for this scenario: {body.description}. "
        f"Stay in character and help the student practice English at {body.cefr_level} level."
    )

    try:
        llm = LLMRouter(db, current_user.id)
        result = await llm.complete_with_fallback(
            messages=[{"role": "user", "content": f"Create a system prompt for this roleplay scenario: {body.description}. The student level is {body.cefr_level}. Respond with ONLY the system prompt text, no JSON."}],
            system_prompt="You are an expert English language tutor. Generate a concise system prompt (2-3 sentences) for a roleplay scenario.",
            task="lesson",
            temperature=0.5,
            max_tokens=300,
        )
        gen_prompt = result["content"].strip()
    except Exception:
        pass

    scenario = Scenario(
        user_id=current_user.id,
        name=body.name,
        system_prompt=gen_prompt,
        cefr_level=body.cefr_level,
        is_custom=True,
    )
    db.add(scenario)
    await db.flush()
    await db.refresh(scenario)
    return ScenarioResponse.model_validate(scenario)


@router.get("/{scenario_id}", response_model=ScenarioResponse)
async def get_scenario(
    scenario_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Scenario).where(
            Scenario.id == scenario_id,
            (Scenario.user_id.is_(None)) | (Scenario.user_id == current_user.id),
        )
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return ScenarioResponse.model_validate(scenario)


@router.delete("/{scenario_id}", status_code=204)
async def delete_scenario(
    scenario_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Scenario).where(Scenario.id == scenario_id, Scenario.user_id == current_user.id)
    )
    scenario = result.scalar_one_or_none()
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if not scenario.is_custom:
        raise HTTPException(status_code=400, detail="Cannot delete predefined scenarios")
    await db.delete(scenario)
