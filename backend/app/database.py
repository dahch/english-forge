from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

# Only SQLite is supported (asyncpg was removed from requirements). Fail fast
# with a clear message instead of crashing later with a cryptic
# ModuleNotFoundError when a stale .env still points at postgresql+asyncpg://.
if not settings.DATABASE_URL.startswith("sqlite"):
    raise ValueError(
        "Only SQLite is supported: unset DATABASE_URL or point it at a "
        f"sqlite+aiosqlite:// URL (got: {settings.DATABASE_URL})"
    )

# SQLite needs explicit concurrency settings: WAL allows concurrent readers
# with a single writer, and busy_timeout makes writers wait instead of failing
# with "database is locked" under parallel requests (e.g. message POST racing
# the TTS job polling).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args={"timeout": 30},
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    # SQLite defaults FK enforcement to OFF — without this, the ondelete=
    # CASCADE / SET NULL rules in models.py never fire.
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
