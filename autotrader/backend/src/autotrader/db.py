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
        await _create_indices(conn)


async def _create_indices(conn) -> None:  # type: ignore[no-untyped-def]
    """Create composite indices after tables exist.

    Must run after ``create_all`` so the target tables are guaranteed
    to exist. All statements use ``IF NOT EXISTS`` so they are
    idempotent and safe to run on every startup.
    """
    # Phase 2: composite indices on trade_attempts(received_at, dim).
    # Without these the timeseries/breakdown queries do a full table
    # scan on (received_at >= since) for every request.
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_trade_attempts_received_chat "
            "ON trade_attempts(received_at, chat_id)",
        ),
    )
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_trade_attempts_received_parser "
            "ON trade_attempts(received_at, parser_config_id)",
        ),
    )
    # Phase 2 idempotency (audit 2026-05-13, H1): composite index that
    # the dedup query keys on. Filtering by ``chat_id`` + equality on
    # ``tg_message_id`` then bounded ``created_at >=`` is what the
    # pipeline runs on the hot path of every incoming message.
    await conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_trade_attempts_chat_tg_msg "
            "ON trade_attempts(chat_id, tg_message_id)",
        ),
    )


async def _migrate_in_place(conn) -> None:  # type: ignore[no-untyped-def]  # noqa: PLR0912
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
        cols = set()  # treat as fresh after drop

    # ``martingale_auto_recovery`` was added when auto-recovery on loss
    # became opt-in. Existing rows default to 0 (off) so back-compat
    # holds — operators flip it on per parser via the API or directly.
    if cols and "martingale_auto_recovery" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE parser_configs ADD COLUMN "
                "martingale_auto_recovery BOOLEAN NOT NULL DEFAULT 0",
            ),
        )
    if cols and "winning_streak_enabled" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE parser_configs ADD COLUMN "
                "winning_streak_enabled BOOLEAN NOT NULL DEFAULT 0",
            ),
        )
    if cols and "winning_streak_max_level" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE parser_configs ADD COLUMN "
                "winning_streak_max_level INTEGER NOT NULL DEFAULT 2",
            ),
        )

    # global_settings gained columns over time. SQLite's ALTER TABLE
    # ADD COLUMN is safe for nullable / defaulted columns and we only
    # fire it when the column is genuinely missing.
    cols = await conn.run_sync(_columns, "global_settings")
    if cols and "pipeline_active" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "pipeline_active BOOLEAN NOT NULL DEFAULT 0",
            ),
        )
    if cols and "daily_max_loss" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "daily_max_loss REAL NOT NULL DEFAULT 0",
            ),
        )
    if cols and "daily_max_stake" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "daily_max_stake REAL NOT NULL DEFAULT 0",
            ),
        )
    if cols and "max_concurrent_trades" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "max_concurrent_trades INTEGER NOT NULL DEFAULT 0",
            ),
        )
    if cols and "admin_telegram_user_id" not in cols:
        # Phase 8 admin bot: persisted on the singleton settings row so
        # there's no separate table to manage. Nullable INTEGER —
        # ``None`` means unbound, the first /start fills it.
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_telegram_user_id INTEGER NULL",
            ),
        )
    # Per-class notification toggles. Default ON so a freshly bound
    # admin sees the full firehose; operators mute via ``/notify`` from
    # the bot or via the dashboard.
    if cols and "admin_notify_placed" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_placed BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_settled" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_settled BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_risk_rejected" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_risk_rejected BOOLEAN NOT NULL DEFAULT 1",
            ),
        )
    if cols and "admin_notify_system_error" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE global_settings ADD COLUMN "
                "admin_notify_system_error BOOLEAN NOT NULL DEFAULT 1",
            ),
        )

    # Phase 2 idempotency (audit 2026-05-13, H1): trade_attempts grew a
    # ``tg_message_id`` column to dedup pyrogram replays. Legacy rows
    # have no source message id; ``None`` (the SQLite NULL) is fine —
    # the pipeline's dedup query simply won't match them. We also add
    # a composite index on ``(chat_id, tg_message_id)`` because the
    # dedup query keys on both.
    cols = await conn.run_sync(_columns, "trade_attempts")
    if cols and "tg_message_id" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE trade_attempts ADD COLUMN "
                "tg_message_id INTEGER NULL",
            ),
        )

    # martingale_states gained winning-streak columns when Paroli
    # sizing landed. Existing rows default to ``0`` for both, which
    # is the same as having no streak in progress.
    cols = await conn.run_sync(_columns, "martingale_states")
    if cols and "current_win_streak" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE martingale_states ADD COLUMN "
                "current_win_streak INTEGER NOT NULL DEFAULT 0",
            ),
        )
    if cols and "last_payout" not in cols:
        await conn.execute(
            text(
                "ALTER TABLE martingale_states ADD COLUMN "
                "last_payout REAL NOT NULL DEFAULT 0",
            ),
        )


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a single async session per request."""
    async with AsyncSessionLocal() as session:
        yield session
