from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import sqlalchemy as sa
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_column_migration_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(lambda sync_conn: _ensure_new_columns_sync(sync_conn))
        await conn.run_sync(lambda sync_conn: _ensure_indexes_sync(sync_conn))
    logger.info("Database tables created/verified")

    if settings.APP_PIN:
        logger.info("APP_PIN protection is enabled")

    yield

    await engine.dispose()


app = FastAPI(
    title="EnglishForge API",
    description="Personal English practice app — BYOK, self-hosted, multi-user",
    version="1.0.0",
    lifespan=lifespan,
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


def _column_exists(sync_conn, table: str, column_name: str) -> bool:
    inspector = sa.inspect(sync_conn)
    return column_name in {c["name"] for c in inspector.get_columns(table)}


def _ensure_new_columns_sync(sync_conn) -> None:
    inspector = sa.inspect(sync_conn)
    for table, columns in _COLUMNS_TO_ADD.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for column_name, column_def in columns:
            if column_name not in existing:
                # SAVEPOINT keeps the outer transaction usable when a
                # concurrent instance's startup adds the same column first
                # (duplicate-column would otherwise abort the whole tx).
                try:
                    with sync_conn.begin_nested():
                        sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_def}"))
                    logger.info(f"Added missing column {table}.{column_name}")
                # SQLite raises OperationalError for duplicate columns, Postgres
                # raises ProgrammingError — catch both so the race handling
                # below actually fires on both dialects.
                except (sa.exc.ProgrammingError, sa.exc.OperationalError):
                    if _column_exists(sync_conn, table, column_name):
                        logger.info(f"Column {table}.{column_name} already added by a concurrent startup — skipping")
                    else:
                        # A real failure (not a startup race) — e.g. a NOT NULL
                        # column without a default on a non-empty table would
                        # fail permanently. Surface it instead of pretending the
                        # migration succeeded.
                        logger.warning(
                            f"Failed to add column {table}.{column_name} and it is still missing "
                            f"after the attempt. DDL: ALTER TABLE {table} ADD COLUMN {column_name} {column_def}"
                        )


def _index_exists(sync_conn, table_name: str, index_name: str) -> bool:
    if sync_conn.dialect.name == "sqlite":
        rows = sync_conn.execute(text(f"PRAGMA index_list({table_name!r})")).fetchall()
        return any(r[1] == index_name for r in rows)
    rows = sync_conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE tablename = :t AND indexname = :n"),
        {"t": table_name, "n": index_name},
    )
    return rows.first() is not None


# Partial unique indexes enforcing invariants that plain column adds can't
# express. IF NOT EXISTS makes repeated startups idempotent. Each entry is
# (index_name, table_name, create_sql) so failures can be verified, not just
# blamed on a startup race.
_INDEXES_TO_ADD: list[tuple[str, str, str]] = [
    # Only one in-progress assessment per user — makes the duplicate-start
    # guard in POST /api/assessment/start atomic (see start_assessment).
    (
        "uq_assessments_user_in_progress",
        "assessments",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_assessments_user_in_progress "
        "ON assessments (user_id) WHERE completed_at IS NULL",
    ),
    # Only one active learning path per user — makes the deactivate+insert in
    # POST /api/learning-paths/generate atomic (see _generate_path_for_user).
    (
        "uq_learning_paths_user_active",
        "learning_paths",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_paths_user_active "
        "ON learning_paths (user_id) WHERE is_active",
    ),
]


def _ensure_indexes_sync(sync_conn) -> None:
    for index_name, table_name, index_sql in _INDEXES_TO_ADD:
        try:
            with sync_conn.begin_nested():
                sync_conn.execute(text(index_sql))
            logger.info(f"Verified index {index_name}")
        # SQLite raises OperationalError for duplicate-column/UNIQUE-violation
        # DDL, Postgres raises ProgrammingError — catch both so the race
        # handling below actually fires on both dialects.
        except (sa.exc.ProgrammingError, sa.exc.OperationalError):
            if _index_exists(sync_conn, table_name, index_name):
                logger.info(f"Index {index_name} already created by a concurrent startup — skipping")
            else:
                # Creation failed for a real reason (typically pre-existing
                # duplicate rows). These indexes are load-bearing for the
                # atomic duplicate-start guards, so log loudly rather than
                # leaving the invariant silently unprotected.
                logger.warning(
                    f"Failed to create index {index_name} and it is not present — "
                    f"likely pre-existing duplicate rows. The invariant it enforces "
                    f"(one active path / one in-progress assessment per user) is NOT "
                    f"protected. SQL: {index_sql}"
                )


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
