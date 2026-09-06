from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.integrations.crypto import decrypt_value, encrypt_value
from app.models.models import (
    Assessment,
    AssessmentMessage,
    Correction,
    GeneratedLesson,
    LearningPath,
    Message,
    PathLesson,
    ProgressDaily,
    ProviderConfig,
    Scenario,
    Session,
    Setting,
    User,
    VocabItem,
)
from app.schemas.settings import (
    ProviderConfigCreate,
    ProviderConfigResponse,
    ProviderConfigUpdate,
    SettingResponse,
    UserSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/providers", response_model=list[ProviderConfigResponse])
async def list_providers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderConfig)
        .where(ProviderConfig.user_id == current_user.id)
        .order_by(ProviderConfig.priority.desc())
    )
    configs = result.scalars().all()
    responses = []
    for c in configs:
        responses.append(ProviderConfigResponse(
            id=c.id,
            provider_name=c.provider_name,
            base_url=c.base_url,
            model=c.model,
            protocol=c.protocol,
            is_active=c.is_active,
            priority=c.priority,
            task_routing=c.task_routing,
            has_api_key=bool(c.api_key_enc),
        ))
    return responses


@router.post("/providers", response_model=ProviderConfigResponse, status_code=201)
async def create_provider(
    body: ProviderConfigCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.provider_name == body.provider_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Provider already configured. Update it instead.")

    config = ProviderConfig(
        user_id=current_user.id,
        provider_name=body.provider_name,
        api_key_enc=encrypt_value(body.api_key),
        base_url=body.base_url,
        model=body.model,
        protocol=body.protocol,
        is_active=body.is_active,
        priority=body.priority,
        task_routing=body.task_routing,
    )
    db.add(config)
    await db.flush()
    await db.refresh(config)

    return ProviderConfigResponse(
        id=config.id,
        provider_name=config.provider_name,
        base_url=config.base_url,
        model=config.model,
        protocol=config.protocol,
        is_active=config.is_active,
        priority=config.priority,
        task_routing=config.task_routing,
        has_api_key=True,
    )


@router.patch("/providers/{provider_id}", response_model=ProviderConfigResponse)
async def update_provider(
    provider_id: str,
    body: ProviderConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.id == provider_id,
            ProviderConfig.user_id == current_user.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")

    if body.api_key is not None:
        config.api_key_enc = encrypt_value(body.api_key)
    if body.base_url is not None:
        config.base_url = body.base_url
    if body.model is not None:
        config.model = body.model
    if body.protocol is not None:
        config.protocol = body.protocol
    if body.is_active is not None:
        config.is_active = body.is_active
    if body.priority is not None:
        config.priority = body.priority
    if body.task_routing is not None:
        config.task_routing = body.task_routing

    await db.flush()
    await db.refresh(config)

    return ProviderConfigResponse(
        id=config.id,
        provider_name=config.provider_name,
        base_url=config.base_url,
        model=config.model,
        protocol=config.protocol,
        is_active=config.is_active,
        priority=config.priority,
        task_routing=config.task_routing,
        has_api_key=bool(config.api_key_enc),
    )


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.id == provider_id,
            ProviderConfig.user_id == current_user.id,
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Provider not found")
    await db.delete(config)


@router.get("", response_model=list[SettingResponse])
async def get_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Setting).where(Setting.user_id == current_user.id)
    )
    settings = result.scalars().all()
    return [SettingResponse(key=s.key, value=s.value) for s in settings]


@router.patch("", response_model=list[SettingResponse])
async def update_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updates = {}
    if body.stt_mode is not None:
        updates["stt_mode"] = body.stt_mode
    if body.tts_voice is not None:
        updates["tts_voice"] = body.tts_voice
    if body.default_cefr is not None:
        updates["default_cefr"] = body.default_cefr
    if body.task_routing is not None:
        updates["task_routing"] = json.dumps(body.task_routing.model_dump())

    for key, value in updates.items():
        result = await db.execute(
            select(Setting).where(Setting.user_id == current_user.id, Setting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            db.add(Setting(user_id=current_user.id, key=key, value=value))

    await db.flush()

    result = await db.execute(select(Setting).where(Setting.user_id == current_user.id))
    settings = result.scalars().all()
    return [SettingResponse(key=s.key, value=s.value) for s in settings]


@router.delete("/clear-data", status_code=200)
async def clear_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all user learning data while preserving the account and provider configs.

    Clears: sessions, messages, corrections, vocab, scenarios, assessments,
    generated lessons, learning paths, progress entries, and settings.
    """
    user_id = current_user.id
    deleted_counts: dict[str, int] = {}

    # Delete children before parents to respect foreign key constraints.
    # ORM cascade rules only apply when loading/deleting objects through the
    # session identity map; bulk deletes bypass those cascades, so we must
    # delete in the correct SQL order.

    # Corrections reference messages.
    deleted_counts["corrections"] = (
        await db.execute(
            delete(Correction)
            .where(Correction.message_id.in_(
                select(Message.id).where(Message.session_id.in_(
                    select(Session.id).where(Session.user_id == user_id)
                ))
            ))
            .execution_options(synchronize_session=False)
        )
    ).rowcount

    # Vocab items may reference messages.
    deleted_counts["vocab_items"] = (
        await db.execute(
            delete(VocabItem).where(VocabItem.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Assessment messages reference assessments.
    deleted_counts["assessment_messages"] = (
        await db.execute(
            delete(AssessmentMessage)
            .where(AssessmentMessage.assessment_id.in_(
                select(Assessment.id).where(Assessment.user_id == user_id)
            ))
            .execution_options(synchronize_session=False)
        )
    ).rowcount

    # Path lessons reference learning paths.
    deleted_counts["path_lessons"] = (
        await db.execute(
            delete(PathLesson)
            .where(PathLesson.path_id.in_(
                select(LearningPath.id).where(LearningPath.user_id == user_id)
            ))
            .execution_options(synchronize_session=False)
        )
    ).rowcount

    # Messages reference sessions.
    deleted_counts["messages"] = (
        await db.execute(
            delete(Message)
            .where(Message.session_id.in_(
                select(Session.id).where(Session.user_id == user_id)
            ))
            .execution_options(synchronize_session=False)
        )
    ).rowcount

    # Sessions reference users and scenarios.
    deleted_counts["sessions"] = (
        await db.execute(
            delete(Session).where(Session.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Learning paths reference users (and assessments with SET NULL).
    deleted_counts["learning_paths"] = (
        await db.execute(
            delete(LearningPath).where(LearningPath.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Assessments reference users.
    deleted_counts["assessments"] = (
        await db.execute(
            delete(Assessment).where(Assessment.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Generated lessons reference users.
    deleted_counts["generated_lessons"] = (
        await db.execute(
            delete(GeneratedLesson).where(GeneratedLesson.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Scenarios reference users.
    deleted_counts["scenarios"] = (
        await db.execute(
            delete(Scenario).where(Scenario.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Progress entries reference users.
    deleted_counts["progress_daily"] = (
        await db.execute(
            delete(ProgressDaily).where(ProgressDaily.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Settings reference users (but NOT provider configs).
    deleted_counts["settings"] = (
        await db.execute(
            delete(Setting).where(Setting.user_id == user_id).execution_options(synchronize_session=False)
        )
    ).rowcount

    # Reset user level and assessment status so the frontend starts fresh.
    current_user.current_level = "B1"
    current_user.assessment_completed = False

    await db.commit()

    logger.info(
        f"Cleared all data for user {user_id}: "
        + ", ".join(f"{table}={count}" for table, count in deleted_counts.items())
    )
    return {
        "message": "All user data cleared successfully",
        "deleted": deleted_counts,
    }
