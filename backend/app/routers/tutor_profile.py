from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import TutorProfile, User

router = APIRouter(prefix="/api/tutor-profile", tags=["tutor-profile"])


class TutorProfileUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    age: int | None = Field(None, ge=18, le=120)
    gender: str | None = Field(None, max_length=50)
    personality: str | None = Field(None, max_length=100)
    voice: str | None = Field(None, max_length=100)


class TutorProfileResponse(BaseModel):
    id: str
    user_id: str
    name: str
    age: int | None
    gender: str | None
    personality: str
    voice: str | None

    model_config = {"from_attributes": True}


async def _get_or_create_profile(db: AsyncSession, user: User) -> TutorProfile:
    result = await db.execute(select(TutorProfile).where(TutorProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = TutorProfile(
            user_id=user.id,
            name=user.display_name or "Sarah",
            personality="friendly",
        )
        db.add(profile)
        await db.flush()
        await db.refresh(profile)
    return profile


@router.get("", response_model=TutorProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_or_create_profile(db, current_user)
    return profile


@router.patch("", response_model=TutorProfileResponse)
async def update_profile(
    body: TutorProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_or_create_profile(db, current_user)

    # model_fields_set distinguishes "field absent" from "field explicitly
    # sent as null", so the Settings page can clear age/gender/voice.
    # name/personality are NOT NULL columns — ignore explicit nulls for them.
    nullable_fields = {"age", "gender", "voice"}
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is None and field not in nullable_fields:
            continue
        setattr(profile, field, value)

    await db.flush()
    await db.refresh(profile)
    return profile
