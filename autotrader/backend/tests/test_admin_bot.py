"""Admin Telegram bot — unit + integration tests."""

from __future__ import annotations

import asyncio
from typing import Any

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


def test_admin_bot_router_reports_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415
    from tests.test_pipeline import _login  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.main import app  # noqa: PLC0415
    with TestClient(app) as c:
        headers = _login(c)
        r = c.get("/admin-bot/status", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "disabled"
        assert body["bound_user_id"] is None


def test_admin_bot_unbind_clears_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415
    from tests.test_pipeline import _login  # noqa: PLC0415

    monkeypatch.setattr("autotrader.services.quotex_manager.Quotex", FakeQuotex)

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _seed_bound_admin() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            if gs is None:
                gs = GlobalSettings(id=1)
            gs.admin_telegram_user_id = 12345
            s.add(gs)
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed_bound_admin())

    with TestClient(app) as c:
        headers = _login(c)
        r = c.post("/admin-bot/unbind", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["bound_user_id"] is None

        # The persisted row must reflect the unbind.
        async def _read() -> int | None:
            async with AsyncSessionLocal() as s:
                gs = await s.get(GlobalSettings, 1)
                return gs.admin_telegram_user_id if gs else None
        assert asyncio.new_event_loop().run_until_complete(_read()) is None


def test_first_start_binds_admin_user_id() -> None:
    """The first ``/start`` from any user binds that user_id to the
    settings row AND to the in-memory bot, then replies confirm."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> tuple[int | None, list[tuple[int, str, object]]]:
        await bot.start()
        # First /start from user 555 binds.
        await fake.fire_message(user_id=555, text="/start")
        return bot.status().bound_user_id, list(fake.sent_messages)

    bound, sent = asyncio.new_event_loop().run_until_complete(_run())
    assert bound == 555
    # Exactly one reply, confirming the bind.
    assert len(sent) == 1
    chat_id, text, _ = sent[0]
    assert chat_id == 555
    assert "bound" in text.lower()


def test_second_start_from_other_user_is_rejected() -> None:
    """Once bound, /start from a *different* user replies 'bound to
    another admin' and does NOT change the bound id."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _seed_first() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            s.add(gs); await s.commit()

    asyncio.new_event_loop().run_until_complete(_seed_first())

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> tuple[int | None, list[tuple[int, str, object]]]:
        await bot.start()
        await fake.fire_message(user_id=999, text="/start")
        return bot.status().bound_user_id, list(fake.sent_messages)

    bound, sent = asyncio.new_event_loop().run_until_complete(_run())
    assert bound == 555  # unchanged
    assert len(sent) == 1
    chat_id, text, _ = sent[0]
    assert chat_id == 999
    assert "another admin" in text.lower()


def test_non_admin_message_is_silently_dropped() -> None:
    """Any *non-/start* message from a non-admin user must be silently
    dropped — no reply, no log noise to the user."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))

    async def _run() -> list[tuple[int, str, object]]:
        await bot.start()
        await fake.fire_message(user_id=999, text="/status")
        return list(fake.sent_messages)

    sent = asyncio.new_event_loop().run_until_complete(_run())
    assert sent == []


def _make_bound_bot() -> tuple[Any, Any]:
    """Helper: a started bot bound to user 555."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import build_message_hook  # noqa: PLC0415

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))
    return bot, fake


def test_help_lists_commands() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/help")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    for command in ("/status", "/balance", "/killswitch", "/channels", "/parsers"):
        assert command in text, f"{command} missing from /help text"


def test_whoami_echoes_user_id() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/whoami")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "555" in text


def test_status_includes_pipeline_kill_switch_broker() -> None:
    """The /status command must mention all three core gauges."""
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/status")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    for label in ("pipeline", "kill switch", "broker"):
        assert label.lower() in text.lower()


def test_trades_renders_last_n() -> None:
    """``/trades 3`` reads the most recent 3 trade attempts and renders
    a one-line-per-row summary including asset / direction / outcome."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_fetch() -> str:
        async with AsyncSessionLocal() as s:
            for asset, direction, status_, profit in [
                ("EURUSD_otc", "call", "won", 1.8),
                ("GBPUSD_otc", "put", "lost", -1.0),
                ("USDJPY_otc", "call", "pending", None),
            ]:
                s.add(TradeAttempt(
                    chat_id=-9001,  # unique to avoid bleeding into test_pipeline
                    parser_config_id=9001,
                    asset=asset,
                    asset_raw=asset,
                    direction=direction,
                    duration_seconds=60,
                    stake=1.0,
                    trade_mode="live",  # required field (plan omitted it)
                    status=status_,
                    profit=profit,
                ))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/trades 3")
        return fake.sent_messages[-1][1]

    async def _cleanup() -> None:
        # Delete the seeded trades so they don't bleed into other test
        # files that share this conftest's tempfile-backed SQLite DB
        # and assert "no trades yet" / specific counts.
        from sqlmodel import delete  # noqa: PLC0415
        async with AsyncSessionLocal() as s:
            await s.exec(delete(TradeAttempt).where(  # type: ignore[call-overload]
                TradeAttempt.chat_id == -9001,
            ))
            await s.commit()

    try:
        text = asyncio.new_event_loop().run_until_complete(_seed_then_fetch())
        assert "EURUSD_otc" in text
        assert "GBPUSD_otc" in text
        assert "USDJPY_otc" in text
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_decisions_renders_recent_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/decisions`` reads from the in-memory ring buffer on the
    Pipeline. We monkeypatch the resolver to return canned decisions."""
    from autotrader.services import admin_bot_commands as cmds  # noqa: PLC0415

    canned = [
        {"ts": "2026-05-09T10:00:00", "chat_id": -1001,
         "parser_config_id": 1, "parser_name": "DreamVIP",
         "parser_type": "regex", "outcome": "matched",
         "reasons": [], "signals": 1, "text_preview": "BUY EURUSD 1m"},
        {"ts": "2026-05-09T10:00:01", "chat_id": -1002,
         "parser_config_id": None, "parser_name": None,
         "parser_type": None, "outcome": "no_configs",
         "reasons": [], "signals": 0, "text_preview": "stray msg"},
    ]
    monkeypatch.setattr(cmds, "_recent_decisions_snapshot", lambda: canned)

    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/decisions 5")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "matched" in text
    assert "no_configs" in text


def test_streaks_lists_per_parser_state() -> None:
    """``/streaks`` reads MartingaleState rows and renders one line per."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from sqlmodel import delete  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_then_fetch() -> str:
        async with AsyncSessionLocal() as s:
            s.add(ParserConfig(
                id=9042, chat_id=-9001, name="DreamVIP",
                parser_type="regex", default_stake=10.0,
                martingale_enabled=True, martingale_multiplier=2.0,
            ))
            s.add(MartingaleState(parser_config_id=9042, current_streak=2, last_stake=40.0))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/streaks")
        return fake.sent_messages[-1][1]

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(MartingaleState).where(  # type: ignore[call-overload]
                MartingaleState.parser_config_id == 9042,
            ))
            await s.exec(delete(ParserConfig).where(  # type: ignore[call-overload]
                ParserConfig.id == 9042,
            ))
            await s.commit()

    try:
        text = asyncio.new_event_loop().run_until_complete(_seed_then_fetch())
        assert "DreamVIP" in text
        assert "2" in text  # the streak number
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def _reset_global_settings_flags() -> None:
    """Reset the kill-switch / pipeline-active flags so toggle tests
    don't leak state into test_pipeline.py (which expects a fresh
    pipeline_active=False default on its first run)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _do() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            if gs is not None:
                gs.kill_switch_engaged = False
                gs.pipeline_active = False
                s.add(gs)
                await s.commit()

    asyncio.new_event_loop().run_until_complete(_do())


def test_killswitch_on_persists_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        await bot.start()
        await fake.fire_message(555, "/killswitch on")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.kill_switch_engaged if gs else False

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) is True
    finally:
        _reset_global_settings_flags()


def test_pipeline_off_persists_flag() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_and_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            s.add(gs)
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/pipeline off")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.pipeline_active if gs else True

    try:
        assert asyncio.new_event_loop().run_until_complete(_seed_and_toggle()) is False
    finally:
        _reset_global_settings_flags()


def test_panic_kills_both() -> None:
    """`/panic` engages kill switch AND turns pipeline off in one shot."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _seed_and_panic() -> tuple[bool, bool]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.pipeline_active = True
            gs.kill_switch_engaged = False
            s.add(gs)
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/panic")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.pipeline_active, gs.kill_switch_engaged

    try:
        pipe, kill = asyncio.new_event_loop().run_until_complete(_seed_and_panic())
        assert pipe is False
        assert kill is True
    finally:
        _reset_global_settings_flags()


def test_mode_real_requires_confirm() -> None:
    """`/mode real` first replies with a confirm keyboard — it does not
    flip the broker until the operator clicks the inline 'Yes'."""
    bot, fake = _make_bound_bot()

    async def _run() -> tuple[str, object]:
        await bot.start()
        await fake.fire_message(555, "/mode real")
        text = fake.sent_messages[-1][1]
        markup = fake.sent_messages[-1][2]
        return text, markup

    text, markup = asyncio.new_event_loop().run_until_complete(_run())
    assert "confirm" in text.lower() or "real" in text.lower()
    assert markup is not None
