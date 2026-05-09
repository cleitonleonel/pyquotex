"""Tests for stats/v2 filter resolution and index presence (Phase 2)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


pytestmark = pytest.mark.asyncio


async def test_phase2_indices_present_after_init() -> None:
    """The two composite indices land via the in-place migration block.

    Uses a fresh engine built from the environment URL so the test is
    not affected by the module-reload trick in test_admin_bot that
    leaves autotrader.db.engine pointing at a stale legacy.db fixture.
    init_db() is replicated inline: _migrate_in_place + create_all +
    _create_indices, using our own connection so we control the target.
    """
    import autotrader.models  # noqa: PLC0415, F401 — registers models on SQLModel.metadata
    from autotrader.db import _create_indices, _migrate_in_place  # noqa: PLC0415

    db_url = os.environ["AUTOTRADER_DB_URL"]
    engine = create_async_engine(db_url, future=True)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with engine.begin() as conn:
            await _migrate_in_place(conn)
            await conn.run_sync(SQLModel.metadata.create_all)
            await _create_indices(conn)

        async with SessionLocal() as s:
            rows = (
                await s.exec(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index' AND tbl_name = 'trade_attempts'",
                    ),
                )
            ).all()
    finally:
        await engine.dispose()

    names = {r[0] for r in rows}
    assert "ix_trade_attempts_received_chat" in names
    assert "ix_trade_attempts_received_parser" in names
