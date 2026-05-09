"""Tests for stats/v2 filter resolution and index presence (Phase 2)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


@pytest.mark.asyncio
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


from datetime import UTC, datetime, timedelta

from autotrader.services.filters import (
    parse_csv_int,
    parse_csv_str,
    resolve_range,
)


@pytest.mark.parametrize(
    "label,expected_delta",
    [
        ("24h", timedelta(hours=24)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
    ],
)
def test_resolve_range_presets(label: str, expected_delta: timedelta) -> None:
    """Preset range labels map to the right (since, until) span."""
    until = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    since, computed_until = resolve_range(label, now=until)
    assert computed_until == until
    assert until - since == expected_delta


def test_resolve_range_all_returns_epoch_since() -> None:
    until = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    since, _ = resolve_range("all", now=until)
    assert since == datetime(1970, 1, 1, tzinfo=UTC)


def test_resolve_range_custom_uses_explicit_bounds() -> None:
    since_arg = datetime(2026, 5, 1, tzinfo=UTC)
    until_arg = datetime(2026, 5, 8, tzinfo=UTC)
    since, until = resolve_range(
        "custom",
        now=datetime.now(UTC),
        custom_from=since_arg,
        custom_to=until_arg,
    )
    assert since == since_arg
    assert until == until_arg


def test_resolve_range_invalid_label_falls_back_to_24h() -> None:
    """Unknown label silently falls back to 24h (defensive)."""
    until = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
    since, _ = resolve_range("nonsense", now=until)  # type: ignore[arg-type]
    assert until - since == timedelta(hours=24)


def test_parse_csv_int_handles_empty_and_garbage() -> None:
    assert parse_csv_int(None) == []
    assert parse_csv_int("") == []
    assert parse_csv_int("   ") == []
    assert parse_csv_int("1,2,3") == [1, 2, 3]
    assert parse_csv_int(" 12 , 34 ") == [12, 34]
    # Garbage values silently dropped (stale URL safety).
    assert parse_csv_int("1,foo,3") == [1, 3]


def test_parse_csv_str_handles_empty_and_whitespace() -> None:
    assert parse_csv_str(None) == []
    assert parse_csv_str("EURUSD,GBPJPY") == ["EURUSD", "GBPJPY"]
    assert parse_csv_str(" EURUSD , GBPJPY ") == ["EURUSD", "GBPJPY"]
    assert parse_csv_str("") == []
