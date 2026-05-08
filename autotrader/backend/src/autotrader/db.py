"""Async SQLModel + SQLite engine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

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
        await _migrate_in_place(conn)
        await conn.run_sync(SQLModel.metadata.create_all)


async def _migrate_in_place(conn) -> None:  # type: ignore[no-untyped-def]
    """One-off in-place migrations.

    SQLModel ``create_all`` only creates missing tables — it never
    alters an existing one. When we change the shape of a table during
    development the simplest path is: detect the old shape and drop the
    table here so the subsequent create_all recreates it from the
    current model. Any rows are lost; tell the user in the commit
    message and the dashboard.

    Each block is idempotent — once the new shape is in place the
    detector returns False and we move on.
    """
    def _has_table(sync_conn: object, name: str) -> bool:
        return inspect(sync_conn).has_table(name)  # type: ignore[arg-type]

    def _columns(sync_conn: object, name: str) -> set[str]:
        if not _has_table(sync_conn, name):
            return set()
        return {c["name"] for c in inspect(sync_conn).get_columns(name)}  # type: ignore[arg-type]

    # parser_configs grew a row id + multi-per-chat support; the old
    # singleton schema had ``chat_id`` as the primary key. Drop the
    # table when we see the legacy shape.
    cols = await conn.run_sync(_columns, "parser_configs")
    if cols and "id" not in cols:
        await conn.execute(text("DROP TABLE parser_configs"))

    # global_settings gained ``pipeline_active`` in Phase 4. SQLite's
    # ALTER TABLE ADD COLUMN is safe for nullable / defaulted columns
    # and we only fire it when the column is genuinely missing.
    cols = await conn.run_sync(_columns, "global_settings")
    if cols and "pipeline_active" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "pipeline_active BOOLEAN NOT NULL DEFAULT 0",
            ),
        )


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a single async session per request."""
    async with AsyncSessionLocal() as session:
        yield session
