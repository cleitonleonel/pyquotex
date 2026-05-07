"""Async SQLModel + SQLite engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from autotrader.config import settings


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory for a file-backed SQLite URL."""
    if not url.startswith("sqlite") or ":memory:" in url:
        return
    # sqlite+aiosqlite:///./data/autotrader.db -> ./data/autotrader.db
    db_path = url.split("///", 1)[-1]
    if not db_path:
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.db_url)

engine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create tables for any imported SQLModel."""
    # Lazy import: registers models on SQLModel.metadata without
    # creating an import cycle at module load.
    from autotrader import models  # noqa: F401, PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a single async session per request."""
    async with AsyncSessionLocal() as session:
        yield session
