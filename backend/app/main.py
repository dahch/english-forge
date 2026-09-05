from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.models.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="EnglishForge API",
    description="Personal English practice app — BYOK, self-hosted, multi-user",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Columns added to pre-existing tables after initial deployment.
# Base.metadata.create_all only creates missing tables — it never alters
# existing ones — so these must be added explicitly. Each add is guarded
# by an inspector check, making this safe to run on every startup.
_COLUMNS_TO_ADD: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("current_level", "VARCHAR(2) NOT NULL DEFAULT 'B1'"),
        ("assessment_completed", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ],
    "progress_daily": [
        ("lessons_completed", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def _ensure_new_columns_sync(sync_conn) -> None:
    import sqlalchemy as sa

    inspector = sa.inspect(sync_conn)
    for table, columns in _COLUMNS_TO_ADD.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for column_name, column_def in columns:
            if column_name not in existing:
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_def}"))
                logger.info(f"Added missing column {table}.{column_name}")


def _validate_column_migration_metadata() -> None:
    """Guard against drift between _COLUMNS_TO_ADD and the ORM models.

    The startup ALTER TABLE list is a second schema truth next to models.py;
    warn loudly if it references a table/column the models no longer define,
    so a rename in models.py never leaves a silently stale migration entry.
    """
    for table_name, columns in _COLUMNS_TO_ADD.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            logger.warning(
                f"_COLUMNS_TO_ADD references table '{table_name}' which is missing from models.py — stale migration entry"
            )
            continue
        for column_name, _ in columns:
            if column_name not in table.columns:
                logger.warning(
                    f"_COLUMNS_TO_ADD references column {table_name}.{column_name} which is missing from models.py — stale migration entry"
                )


@app.on_event("startup")
async def startup():
    _validate_column_migration_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(lambda sync_conn: _ensure_new_columns_sync(sync_conn))
    logger.info("Database tables created/verified")

    if settings.APP_PIN:
        logger.info("APP_PIN protection is enabled")


@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


from app.routers.auth import router as auth_router
from app.routers.sessions import router as sessions_router
from app.routers.messages import router as messages_router
from app.routers.vocab import router as vocab_router
from app.routers.scenarios import router as scenarios_router
from app.routers.settings import router as settings_router
from app.routers.dashboard import router as dashboard_router
from app.routers.lessons import router as lessons_router
from app.routers.ws import router as ws_router
from app.routers.tutor_profile import router as tutor_profile_router
from app.routers.assessment import router as assessment_router
from app.routers.learning_paths import router as learning_paths_router

app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(vocab_router)
app.include_router(scenarios_router)
app.include_router(settings_router)
app.include_router(dashboard_router)
app.include_router(lessons_router)
app.include_router(ws_router)
app.include_router(tutor_profile_router)
app.include_router(assessment_router)
app.include_router(learning_paths_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
