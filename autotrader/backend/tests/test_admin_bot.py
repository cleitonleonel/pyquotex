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


def test_admin_bot_no_token_is_no_op() -> None:
    """Without ``TELEGRAM_BOT_TOKEN`` set, ``start()`` must complete
    silently and leave the bot in ``state="disabled"``. A missing token
    is the most common 'I haven't set up the bot yet' state — it
    must not crash startup."""
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    bot = AdminBot(bot_token=None)

    async def _run() -> None:
        await bot.start()
        assert bot.status().state == "disabled"
        assert bot.status().bound_user_id is None
        await bot.stop()  # idempotent on disabled

    asyncio.new_event_loop().run_until_complete(_run())


def test_admin_bot_starts_with_fake_client() -> None:
    """When a token *and* a client factory are provided, ``start()``
    constructs the client, calls ``start()`` on it, and reports
    ``state="running"``."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(
        bot_token="123:abc",
        client_factory=lambda token: fake,
    )

    async def _run() -> None:
        await bot.start()
        assert fake.started is True
        assert bot.status().state == "running"
        await bot.stop()
        assert fake.started is False
        assert bot.status().state == "stopped"

    asyncio.new_event_loop().run_until_complete(_run())


def test_admin_bot_start_failure_sets_error_state() -> None:
    """If ``client.start()`` raises (bad token, network) the bot ends
    in ``state="error"`` with ``last_error`` populated, but no exception
    propagates to the caller — startup must not crash the app."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415

    class _BoomFake(FakePyrogramBot):
        async def start(self) -> None:  # type: ignore[override]
            raise RuntimeError("invalid token")

    bot = AdminBot(
        bot_token="123:abc",
        client_factory=lambda token: _BoomFake(),
    )

    async def _run() -> None:
        await bot.start()
        st = bot.status()
        assert st.state == "error"
        assert "invalid token" in (st.last_error or "")

    asyncio.new_event_loop().run_until_complete(_run())


def test_admin_bot_attached_to_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan must attach an ``AdminBot`` instance to
    ``app.state.admin_bot`` so routers + the notifier can find it."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.main import app  # noqa: PLC0415
    with TestClient(app):
        bot = app.state.admin_bot
        # No token -> disabled, but the instance is still attached.
        assert bot.status().state == "disabled"
