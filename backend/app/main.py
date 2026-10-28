from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import async_session_factory, engine
from app.integrations.crypto import decrypt_value, encryption_mode
from app.models.models import Base, ProviderConfig
from app.security import validate_security_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Security first: never touch the database (or serve a request) with a
    # missing/weak signing secret or a malformed encryption key.
    validate_security_settings(settings)
    logger.info("Security settings validated (API-key encryption: %s)", encryption_mode(settings))
    if encryption_mode(settings) == "derived":
        logger.warning(
            "SETTINGS_ENCRYPTION_KEY is not set: provider keys are encrypted with a key "
            "derived from JWT_SECRET_KEY. Rotating JWT_SECRET_KEY will make them unreadable."
        )

    _validate_column_migration_metadata()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(lambda sync_conn: _ensure_new_columns_sync(sync_conn))
        await conn.run_sync(lambda sync_conn: _ensure_indexes_sync(sync_conn))
        await conn.run_sync(lambda sync_conn: _ensure_alembic_baseline_sync(sync_conn))
    logger.info("Database tables created/verified")

    await _warn_undecryptable_provider_keys()

    if settings.APP_PIN:
        logger.info("APP_PIN protection is enabled")

    yield

    await engine.dispose()


async def _warn_undecryptable_provider_keys() -> None:
    """Surface a key/secret mismatch before users hit provider failures.

    A rotated encryption key (or a JWT secret rotation under the legacy derived
    scheme) makes every stored provider key undecryptable. Requests then fail
    with a generic "no providers configured", which is impossible to diagnose.
    """
    try:
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    sa.select(
                        ProviderConfig.provider_name, ProviderConfig.api_key_enc
                    )
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - diagnostic only, never fatal
        logger.warning("Could not verify stored provider keys: %s", exc)
        return

    broken: list[str] = []
    for name, encrypted in rows:
        if not encrypted:
            continue
        try:
            decrypt_value(encrypted)
        except Exception:  # noqa: BLE001 - any decrypt error means broken key
            broken.append(name)
    if broken:
        logger.error(
            "Provider API keys that cannot be decrypted with the current key: %s. "
            "Restore the previous SETTINGS_ENCRYPTION_KEY/JWT_SECRET_KEY or re-add "
            "those providers in Settings.",
            ", ".join(sorted(set(broken))),
        )


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
        ("current_level", "VARCHAR(2) NOT NULL DEFAULT 'A1'"),
        ("assessment_completed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        # Access tokens embed this value; bumping it revokes every session
        # without a denylist (ADR-022).
        ("token_version", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "progress_daily": [
        ("lessons_completed", "INTEGER NOT NULL DEFAULT 0"),
    ],
    # Assessment v2: multi-skill sections (audio listening + pronunciation).
    # Legacy rows (pre-v2) map to 'conversation' — they were pure chat.
    "assessments": [
        ("phase", "VARCHAR(20) NOT NULL DEFAULT 'conversation'"),
        ("section_step", "INTEGER NOT NULL DEFAULT 0"),
        ("dimension_scores", "TEXT"),
        ("dimension_bands", "TEXT"),
        # Abandoning frees the in-progress unique index without faking a result
        # (see DELETE /api/assessment/{id}).
        ("abandoned_at", "DATETIME"),
    ],
    "assessment_messages": [
        ("kind", "VARCHAR(20) NOT NULL DEFAULT 'chat'"),
        ("audio_url", "TEXT"),
        ("metrics", "TEXT"),
        # item_id backs the uq_assessment_messages_user_item unique index
        # (one answer per banked item, enforced atomically).
        ("item_id", "VARCHAR(36)"),
    ],
    # NOTE: assessment_messages.created_at is not listed here — its default
    # changed from SQLite func.now() (second precision, server-side) to a
    # client-side microsecond default in the model. The column type is
    # unchanged, so no DDL is needed; legacy rows keep server timestamps while
    # new rows get microsecond precision. (created_at, id) ordering works for
    # both — the microsecond default just removes the random-UUID tiebreak
    # for same-second inserts.
}


# Revision stamped on databases that predate Alembic. The baseline migration is
# a no-op because create_all already owns creation; stamping makes future
# revisions applicable to both fresh and existing databases.
BASELINE_REVISION = "0001_baseline"


def _ensure_alembic_baseline_sync(sync_conn) -> None:
    inspector = sa.inspect(sync_conn)
    if inspector.has_table("alembic_version"):
        return
    if not inspector.has_table("users"):
        return
    try:
        with sync_conn.begin_nested():
            sync_conn.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            sync_conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": BASELINE_REVISION},
            )
        logger.info("Stamped Alembic baseline %s", BASELINE_REVISION)
    except sa.exc.OperationalError:
        if sa.inspect(sync_conn).has_table("alembic_version"):
            logger.info("Alembic baseline already stamped by a concurrent startup")
        else:
            logger.warning(
                "Could not stamp the Alembic baseline; future migrations will "
                "need a manual 'alembic stamp %s'",
                BASELINE_REVISION,
            )


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
                # SQLite raises OperationalError for duplicate columns —
                # catch it so the race handling below actually fires.
                except sa.exc.OperationalError:
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
    rows = sync_conn.execute(text(f"PRAGMA index_list({table_name!r})")).fetchall()
    return any(r[1] == index_name for r in rows)


def _existing_index_sql(sync_conn, index_name: str) -> str | None:
    row = sync_conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": index_name},
    ).first()
    return row[0] if row else None


def _normalize_sql(sql: str) -> str:
    """Compare index definitions ignoring formatting and IF NOT EXISTS.

    SQLite stores the CREATE statement without `IF NOT EXISTS`, so comparing
    the raw text would report a difference on every startup and needlessly
    drop/recreate the index (a write lock on every boot).
    """
    normalized = " ".join(sql.lower().split())
    return normalized.replace("if not exists ", "")


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
        "ON assessments (user_id) WHERE completed_at IS NULL AND abandoned_at IS NULL",
    ),
    # Only one active learning path per user — makes the deactivate+insert in
    # POST /api/learning-paths/generate atomic (see _generate_path_for_user).
    (
        "uq_learning_paths_user_active",
        "learning_paths",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_paths_user_active "
        "ON learning_paths (user_id) WHERE is_active",
    ),
    # One answer per banked item per assessment (user answers only). Makes the
    # stale/duplicate guard in the listening/speaking handlers atomic: two
    # concurrent /message calls for the same item can't both insert — the loser
    # hits IntegrityError and is dropped. See _handle_listening/_handle_speaking.
    (
        "uq_assessment_messages_user_item",
        "assessment_messages",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_assessment_messages_user_item "
        "ON assessment_messages (assessment_id, item_id) "
        "WHERE role = 'user' AND item_id IS NOT NULL",
    ),
    # One vocabulary row per word per user, case-insensitively. The LLM and the
    # manual form used to race on SELECT-then-INSERT and create duplicates.
    (
        "uq_vocab_items_user_word",
        "vocab_items",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vocab_items_user_word "
        "ON vocab_items (user_id, lower(word))",
    ),
    # The seeded predefined scenarios must exist once, not once per concurrent
    # first request (ADR-023): global rows only.
    (
        "uq_scenarios_global_name",
        "scenarios",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scenarios_global_name "
        "ON scenarios (lower(name)) WHERE user_id IS NULL AND is_custom = 0",
    ),
]


def _ensure_indexes_sync(sync_conn) -> None:
    inspector = sa.inspect(sync_conn)
    for index_name, table_name, index_sql in _INDEXES_TO_ADD:
        if not inspector.has_table(table_name):
            continue
        # IF NOT EXISTS would silently keep an outdated predicate (e.g. the old
        # assessment index without `abandoned_at`). Replace it when the stored
        # definition differs from the one this build expects.
        existing_sql = _existing_index_sql(sync_conn, index_name)
        if existing_sql and _normalize_sql(existing_sql) != _normalize_sql(index_sql):
            try:
                with sync_conn.begin_nested():
                    sync_conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
                logger.info(f"Replacing index {index_name}: definition changed")
            except sa.exc.OperationalError:
                logger.warning(
                    f"Could not drop outdated index {index_name}; keeping the previous "
                    f"definition. Expected: {index_sql}"
                )
                continue
        try:
            with sync_conn.begin_nested():
                sync_conn.execute(text(index_sql))
            logger.info(f"Verified index {index_name}")
        # SQLite raises OperationalError for duplicate-column/UNIQUE-violation
        # DDL — catch it so the race handling below actually fires.
        except sa.exc.OperationalError:
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
from app.routers.export import router as export_router

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
app.include_router(export_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
