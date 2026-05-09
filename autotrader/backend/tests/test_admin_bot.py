"""Admin Telegram bot — unit + integration tests."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from autotrader.models.settings import GlobalSettings


def test_global_settings_has_admin_fields_with_safe_defaults() -> None:
    """Fresh ``GlobalSettings`` row defaults to:
    - admin unbound (``admin_telegram_user_id is None``)
    - all four notify classes ON

    Defaults matter: a brand-new install with no bot configured must
    still construct a valid settings row, and once the operator binds
    the admin they should immediately receive the full event firehose
    without flipping four extra toggles.
    """
    s = GlobalSettings()
    assert s.admin_telegram_user_id is None
    assert s.admin_notify_placed is True
    assert s.admin_notify_settled is True
    assert s.admin_notify_risk_rejected is True
    assert s.admin_notify_system_error is True


def test_migration_adds_admin_columns_to_legacy_global_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A pre-existing ``global_settings`` table missing the admin
    columns gets ALTERed in place — no data loss, no manual migration.

    Repro: build the legacy table by hand, run ``init_db``, assert the
    new columns are present and queryable.
    """
    db_file = tmp_path / "legacy.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("AUTOTRADER_DB_URL", db_url)

    # Force fresh module imports so settings re-reads the env.
    import importlib  # noqa: PLC0415
    import autotrader.config as config_mod  # noqa: PLC0415
    importlib.reload(config_mod)
    import autotrader.db as db_mod  # noqa: PLC0415
    importlib.reload(db_mod)

    async def _setup_legacy_then_migrate() -> set[str]:
        # Step A: build the *old* shape directly.
        async with db_mod.engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE global_settings ("
                " id INTEGER PRIMARY KEY,"
                " default_stake REAL NOT NULL DEFAULT 1.0,"
                " default_duration_seconds INTEGER NOT NULL DEFAULT 60,"
                " kill_switch_engaged BOOLEAN NOT NULL DEFAULT 0,"
                " pipeline_active BOOLEAN NOT NULL DEFAULT 0,"
                " daily_max_loss REAL NOT NULL DEFAULT 0,"
                " daily_max_stake REAL NOT NULL DEFAULT 0,"
                " max_concurrent_trades INTEGER NOT NULL DEFAULT 0,"
                " created_at DATETIME NOT NULL,"
                " updated_at DATETIME NOT NULL"
                ")",
            ))
            await conn.execute(text(
                "INSERT INTO global_settings"
                " (id, created_at, updated_at) VALUES"
                " (1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            ))

        # Step B: run init_db which triggers _migrate_in_place.
        await db_mod.init_db()

        # Step C: read back the column set.
        from sqlalchemy import inspect  # noqa: PLC0415
        async with db_mod.engine.begin() as conn:
            cols = await conn.run_sync(
                lambda sc: {c["name"] for c in inspect(sc).get_columns("global_settings")},
            )
        await db_mod.close_db()
        return cols

    cols = asyncio.new_event_loop().run_until_complete(_setup_legacy_then_migrate())

    assert "admin_telegram_user_id" in cols
    assert "admin_notify_placed" in cols
    assert "admin_notify_settled" in cols
    assert "admin_notify_risk_rejected" in cols
    assert "admin_notify_system_error" in cols
