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
    """Reset GlobalSettings to its default-value shape so toggle / caps
    / stake tests don't leak state into test_pipeline.py (which expects
    pipeline_active=False, killswitch=False, uncapped (0) caps)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _do() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            if gs is not None:
                gs.kill_switch_engaged = False
                gs.pipeline_active = False
                gs.daily_max_loss = 0.0
                gs.daily_max_stake = 0.0
                gs.max_concurrent_trades = 0
                gs.default_stake = 1.0
                gs.admin_notify_placed = True
                gs.admin_notify_settled = True
                gs.admin_notify_risk_rejected = True
                gs.admin_notify_system_error = True
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


def _make_bound_bot_with_callbacks() -> tuple[Any, Any]:
    """Helper: a started bot bound to user 555 with BOTH message + callback
    hooks wired. The message-only ``_make_bound_bot`` would silently drop
    the inline-keyboard callbacks Task 11 introduces."""
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_commands import (  # noqa: PLC0415
        build_callback_hook,
        build_message_hook,
    )

    fake = FakePyrogramBot()
    bot = AdminBot(bot_token="123:abc", client_factory=lambda t: fake, bound_user_id=555)
    bot.set_message_hook(build_message_hook(bot))
    bot.set_callback_hook(build_callback_hook(bot))
    return bot, fake


def test_channels_lists_watched() -> None:
    """`/channels` reads the WatchedChannel rows and renders one line per.
    Uses unique chat_ids (-9001 / -9002) to avoid bleeding into
    test_pipeline.py (which uses -1001 / -1002)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from sqlmodel import delete  # noqa: PLC0415

    bot, fake = _make_bound_bot_with_callbacks()

    async def _seed_and_run() -> str:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-9001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            s.add(WatchedChannel(
                chat_id=-9002, title="Backup", chat_type="channel",
                username="backup", enabled=False,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_message(555, "/channels")
        return fake.sent_messages[-1][1]

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(WatchedChannel).where(  # type: ignore[call-overload]
                WatchedChannel.chat_id.in_([-9001, -9002]),
            ))
            await s.commit()

    try:
        text = asyncio.new_event_loop().run_until_complete(_seed_and_run())
        assert "Signals" in text or "-9001" in text
        assert "Backup" in text or "-9002" in text
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_channel_callback_toggles_enabled_flag() -> None:
    """Tapping the inline Pause button on a channel detail flips
    ``enabled`` from True to False. Uses chat_id -9001 (unique)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from sqlmodel import delete  # noqa: PLC0415

    bot, fake = _make_bound_bot_with_callbacks()

    async def _seed_then_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-9001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_callback(555, "chan:-9001:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(WatchedChannel, -9001)
            return row.enabled if row else True

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(WatchedChannel).where(  # type: ignore[call-overload]
                WatchedChannel.chat_id == -9001,
            ))
            await s.commit()

    try:
        assert asyncio.new_event_loop().run_until_complete(_seed_then_toggle()) is False
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_parser_callback_toggles_enabled_flag() -> None:
    """Tapping the inline Pause button on a parser detail flips
    ``enabled`` from True to False. Uses parser id 9042 + chat_id -9001
    (unique to avoid collision with other tests)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from sqlmodel import delete  # noqa: PLC0415

    bot, fake = _make_bound_bot_with_callbacks()

    async def _seed_then_toggle() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(ParserConfig(
                id=9042, chat_id=-9001, name="DreamVIP",
                parser_type="regex", enabled=True,
            ))
            await s.commit()
        await bot.start()
        await fake.fire_callback(555, "parser:9042:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(ParserConfig, 9042)
            return row.enabled if row else True

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(ParserConfig).where(  # type: ignore[call-overload]
                ParserConfig.id == 9042,
            ))
            await s.commit()

    try:
        assert asyncio.new_event_loop().run_until_complete(_seed_then_toggle()) is False
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_callback_from_unauthorised_user_is_ignored() -> None:
    """A CallbackQuery from a non-bound user must NOT mutate state.
    Uses chat_id -9001 (unique)."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from sqlmodel import delete  # noqa: PLC0415

    bot, fake = _make_bound_bot_with_callbacks()

    async def _seed_then_attack() -> bool:
        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-9001, title="Signals", chat_type="channel",
                username="signals", enabled=True,
            ))
            await s.commit()
        await bot.start()
        # User 999 is NOT bound (555 is). The callback must be ignored.
        await fake.fire_callback(999, "chan:-9001:toggle")
        async with AsyncSessionLocal() as s:
            row = await s.get(WatchedChannel, -9001)
            return row.enabled if row else False

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(WatchedChannel).where(  # type: ignore[call-overload]
                WatchedChannel.chat_id == -9001,
            ))
            await s.commit()

    try:
        assert asyncio.new_event_loop().run_until_complete(_seed_then_attack()) is True
    finally:
        asyncio.new_event_loop().run_until_complete(_cleanup())


def test_caps_loss_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> float:
        await bot.start()
        await fake.fire_message(555, "/caps loss 50")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.daily_max_loss if gs else 0.0

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) == 50.0
    finally:
        _reset_global_settings_flags()


def test_caps_concurrent_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> int:
        await bot.start()
        await fake.fire_message(555, "/caps concurrent 4")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.max_concurrent_trades if gs else 0

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) == 4
    finally:
        _reset_global_settings_flags()


def test_stake_persists_value() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> float:
        await bot.start()
        await fake.fire_message(555, "/stake 7.5")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.default_stake if gs else 0.0

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) == 7.5
    finally:
        _reset_global_settings_flags()


def test_caps_invalid_subcommand_replies_usage() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/caps banana 10")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "usage" in text.lower() or "loss" in text.lower()


def test_notify_settled_off_persists() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        await bot.start()
        await fake.fire_message(555, "/notify settled off")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return gs.admin_notify_settled if gs else True

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) is False
    finally:
        _reset_global_settings_flags()


def test_notify_invalid_class_replies_usage() -> None:
    bot, fake = _make_bound_bot()

    async def _run() -> str:
        await bot.start()
        await fake.fire_message(555, "/notify pancakes off")
        return fake.sent_messages[-1][1]

    text = asyncio.new_event_loop().run_until_complete(_run())
    assert "usage" in text.lower() or "placed" in text


def test_unbind_requires_confirm_then_clears() -> None:
    """`/unbind` first sends a confirm keyboard. The confirm callback
    clears ``admin_telegram_user_id`` AND ``bot.bound_user_id``."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services import admin_bot_state  # noqa: PLC0415

    # We use _make_bound_bot_with_callbacks because the unbind flow
    # requires the callback hook to fire confirm:unbind.
    bot, fake = _make_bound_bot_with_callbacks()
    # Stash the bot in the resolver so the confirm handler can find it.
    admin_bot_state.attach(pipeline=None, quotex=None, admin_bot=bot)

    async def _run() -> tuple[int | None, int | None]:
        await bot.start()
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            s.add(gs); await s.commit()
        await fake.fire_message(555, "/unbind")
        # Click confirm.
        await fake.fire_callback(555, "confirm:unbind")
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            return (
                gs.admin_telegram_user_id if gs else None,
                bot.status().bound_user_id,
            )

    persisted, in_memory = asyncio.new_event_loop().run_until_complete(_run())
    assert persisted is None
    assert in_memory is None


# ---------------------------------------------------------------------------
# Task 14: AdminBotNotifier — token-bucket + send wrapper
# ---------------------------------------------------------------------------


def _reset_notifier_settings() -> None:
    """Like ``_reset_global_settings_flags`` but ALSO clears
    ``admin_telegram_user_id`` back to None — the notifier tests seed
    it to 555 and we don't want that bleeding into ``test_pipeline.py``.
    """
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _do() -> None:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1)
            if gs is not None:
                gs.kill_switch_engaged = False
                gs.pipeline_active = False
                gs.daily_max_loss = 0.0
                gs.daily_max_stake = 0.0
                gs.max_concurrent_trades = 0
                gs.default_stake = 1.0
                gs.admin_notify_placed = True
                gs.admin_notify_settled = True
                gs.admin_notify_risk_rejected = True
                gs.admin_notify_system_error = True
                gs.admin_telegram_user_id = None
                s.add(gs)
                await s.commit()

    asyncio.new_event_loop().run_until_complete(_do())


def test_notifier_sends_when_class_enabled() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    notifier = AdminBotNotifier(bot=bot)

    async def _seed_then_notify() -> list[tuple[int, str, object]]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("placed", "PLACED test")
        return list(fake.sent_messages)

    try:
        sent = asyncio.new_event_loop().run_until_complete(_seed_then_notify())
        assert sent == [(555, "PLACED test", None)]
    finally:
        _reset_notifier_settings()


def test_notifier_skips_when_class_disabled() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    notifier = AdminBotNotifier(bot=bot)

    async def _seed_then_notify() -> list[tuple[int, str, object]]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_settled = False  # muted
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("settled", "WIN test")
        return list(fake.sent_messages)

    try:
        assert asyncio.new_event_loop().run_until_complete(_seed_then_notify()) == []
    finally:
        _reset_notifier_settings()


def test_notifier_skips_when_no_admin_bound() -> None:
    """No-op when ``admin_telegram_user_id`` is None — DM has nowhere
    to land."""
    bot, fake = _make_bound_bot()
    bot.set_bound_user_id(None)
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    notifier = AdminBotNotifier(bot=bot)

    async def _run() -> list:
        from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
        from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = None
            s.add(gs); await s.commit()
        await bot.start()
        await notifier.notify("placed", "test")
        return list(fake.sent_messages)

    try:
        assert asyncio.new_event_loop().run_until_complete(_run()) == []
    finally:
        _reset_notifier_settings()


def test_notifier_rate_limit_coalesces_burst() -> None:
    """After the bucket empties, additional notifications of the same
    class are suppressed until the digest window passes; one digest
    message is sent at the end of the window."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    # Tiny bucket + tiny window so the test runs in <1s.
    notifier = AdminBotNotifier(
        bot=bot, bucket_capacity=2, refill_seconds=10, digest_window=0.1,
    )

    async def _run() -> list[str]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            s.add(gs); await s.commit()
        await bot.start()
        # Fire 5 notifications back-to-back. Bucket starts at 2 -> 2 send
        # through, then 3 are suppressed.
        for i in range(5):
            await notifier.notify("placed", f"#{i}")
        # Wait past the digest window so the suppressed-count message
        # gets emitted.
        await asyncio.sleep(0.15)
        await notifier.flush_digests()
        return [t for _, t, _ in fake.sent_messages]

    try:
        sent = asyncio.new_event_loop().run_until_complete(_run())
        # First two real messages, then one digest.
        assert len([s for s in sent if s.startswith("#")]) == 2
        assert any("suppressed" in s for s in sent), sent
    finally:
        _reset_notifier_settings()


# ---------------------------------------------------------------------------
# Task 15: AdminBotNotifier — bus subscribe + format trade events
# ---------------------------------------------------------------------------


def test_notifier_subscribes_and_renders_trade_upserted() -> None:
    """Publishing a ``trade.upserted`` event with status=pending lands
    a *placed* notification; status=won lands a *settled* notification."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415

    bot, fake = _make_bound_bot()
    bus = TradeEventBus()
    notifier = AdminBotNotifier(bot=bot)

    async def _run() -> list[str]:
        async with AsyncSessionLocal() as s:
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.admin_telegram_user_id = 555
            gs.admin_notify_placed = True
            gs.admin_notify_settled = True
            s.add(gs); await s.commit()
        await bot.start()
        task = asyncio.create_task(notifier.run(bus))

        # Give the subscriber loop a tick to register.
        await asyncio.sleep(0.01)

        # Pending -> placed
        bus.publish("trade.upserted", {
            "id": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration_seconds": 60, "stake": 20.0, "status": "pending",
            "profit": None, "parser_config_id": 4, "trade_mode": "live",
            "martingale_step": 1, "base_stake": 10.0,
        })
        # Won -> settled
        bus.publish("trade.upserted", {
            "id": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration_seconds": 60, "stake": 20.0, "status": "won",
            "profit": 18.4, "parser_config_id": 4, "trade_mode": "live",
            "martingale_step": 1, "base_stake": 10.0,
        })

        # Allow the subscriber to drain.
        await asyncio.sleep(0.05)
        await notifier.shutdown()
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass
        return [t for _, t, _ in fake.sent_messages]

    try:
        sent = asyncio.new_event_loop().run_until_complete(_run())
        assert any("EURUSD_otc" in s for s in sent), sent  # placed notification
        assert any("WON" in s for s in sent), sent  # settled notification
    finally:
        _reset_notifier_settings()


def test_notifier_resets_backoff_on_admin_message() -> None:
    """After the backoff engages, an *incoming* /command from the admin
    must clear it via the message hook."""
    bot, fake = _make_bound_bot()

    async def _run() -> bool:
        from autotrader.services.admin_bot_notify import AdminBotNotifier  # noqa: PLC0415
        from autotrader.services import admin_bot_state  # noqa: PLC0415
        notifier = AdminBotNotifier(bot=bot)
        # Force the backoff state.
        notifier._outbound_paused = True
        notifier._consecutive_failures = 7
        # Stash on the resolver so the hook can find it.
        admin_bot_state.attach(pipeline=None, quotex=None, notifier=notifier)
        await bot.start()
        await fake.fire_message(555, "/whoami")
        return notifier.outbound_paused

    assert asyncio.new_event_loop().run_until_complete(_run()) is False


def test_risk_rejected_event_is_published() -> None:
    """When the risk gate refuses a signal, the executor publishes a
    ``risk.rejected`` event on the bus carrying the rejection reason
    plus the parser/asset/direction context."""
    from sqlmodel import delete  # noqa: PLC0415

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415
    from autotrader.services.parsers.base import ParsedSignal  # noqa: PLC0415

    seen: list[tuple[str, dict[str, Any]]] = []
    bus = TradeEventBus()

    async def _drain() -> None:
        async for event in bus.subscribe():
            seen.append((event.type, dict(event.payload)))
            if any(t == "risk.rejected" for t, _ in seen):
                return

    async def _run() -> int:
        from autotrader.db import AsyncSessionLocal, init_db  # noqa: PLC0415

        # Ensure schema exists when this test runs in isolation (other
        # tests in the suite happen to call init_db via bot startup
        # paths, but this one doesn't go through that flow).
        await init_db()

        async with AsyncSessionLocal() as s:
            s.add(WatchedChannel(
                chat_id=-9001, title="t", chat_type="channel",
                username=None, enabled=True,
            ))
            cfg = ParserConfig(
                id=9043, chat_id=-9001, name="dr", parser_type="regex",
                default_stake=1.0, enabled=True,
            )
            s.add(cfg)
            gs = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            gs.kill_switch_engaged = True
            gs.pipeline_active = True
            s.add(gs)
            await s.commit()
            await s.refresh(cfg)
            cfg_id = cfg.id or 0

        # Stand up a fake quotex manager — enough surface for the
        # executor's "broker_connected" check; the kill switch will
        # block the trade before any real broker call happens.
        class _FakeQM:
            connected = True
            account_mode = "PRACTICE"

            def status(self) -> Any:  # noqa: ANN401
                class _Status:
                    account_mode = "PRACTICE"
                return _Status()

        executor = TradeExecutor(
            manager=_FakeQM(),  # type: ignore[arg-type]
            live_trading_enabled_env=False,
            event_bus=bus,
        )

        signal = ParsedSignal(
            asset="EURUSD_otc", direction="call",
            duration_seconds=60, stake=10.0, fire_at=None,
            raw_text="test", parser_id="x", asset_raw="EURUSD",
        )
        async with AsyncSessionLocal() as s:
            settings_row = await s.get(GlobalSettings, 1)
            assert settings_row is not None

        drain = asyncio.create_task(_drain())
        # Yield once so the drain task gets to register its subscriber
        # queue on the bus before submit() publishes.
        await asyncio.sleep(0)
        await executor.submit(
            signal=signal, parser_config=cfg, settings=settings_row,
        )
        await asyncio.wait_for(drain, timeout=2.0)
        return cfg_id

    async def _cleanup(cfg_id: int) -> None:
        from autotrader.db import AsyncSessionLocal  # noqa: PLC0415

        async with AsyncSessionLocal() as s:
            await s.exec(delete(TradeAttempt).where(  # type: ignore[call-overload]
                TradeAttempt.chat_id == -9001,
            ))
            await s.exec(delete(ParserConfig).where(  # type: ignore[call-overload]
                ParserConfig.id == cfg_id,
            ))
            await s.exec(delete(WatchedChannel).where(  # type: ignore[call-overload]
                WatchedChannel.chat_id == -9001,
            ))
            await s.commit()

    cfg_id = 0
    try:
        cfg_id = asyncio.new_event_loop().run_until_complete(_run())
    finally:
        if cfg_id:
            asyncio.new_event_loop().run_until_complete(_cleanup(cfg_id))
        _reset_global_settings_flags()

    types = [t for t, _ in seen]
    assert "risk.rejected" in types, f"saw events: {types}"
    payload = next(p for t, p in seen if t == "risk.rejected")
    assert payload["asset"] == "EURUSD_otc"
    assert payload["direction"] == "call"
    assert payload["chat_id"] == -9001
    assert payload["parser_config_id"] == cfg_id
    assert payload["parser_name"] == "dr"
    assert "kill switch" in payload["reason"].lower()


def test_quotex_manager_publishes_system_error_on_disconnect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``QuotexManager.disconnect()`` raises (broker IO failure)
    the manager must emit a ``system.error`` event so the notifier
    can DM the admin."""
    from autotrader.services.event_bus import TradeEventBus  # noqa: PLC0415
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", FakeQuotex,
    )
    bus = TradeEventBus()
    seen: list[dict] = []

    async def _run() -> None:
        async def _drain() -> None:
            async for event in bus.subscribe():
                if event.type == "system.error":
                    seen.append(dict(event.payload))
                    return
        manager = QuotexManager(root_path=".", event_bus=bus)
        manager.set_credentials("a@b.c", "p", "PRACTICE")
        manager.begin_connect()
        await manager.wait_settled(timeout=1.0)
        # Force a disconnect failure by swapping out the inner client
        # for one whose ``close_connection`` raises.
        class _BoomClient:
            async def close_connection(self) -> None:
                raise RuntimeError("simulated broker io failure")
            check_connect = False
        manager._client = _BoomClient()  # type: ignore[attr-defined]

        drain = asyncio.create_task(_drain())
        # Yield once so ``_drain`` can enter ``bus.subscribe()`` and
        # register its queue before the publish fires synchronously
        # inside ``disconnect()``.
        await asyncio.sleep(0)
        await manager.disconnect()
        await asyncio.wait_for(drain, timeout=1.0)

    asyncio.new_event_loop().run_until_complete(_run())
    assert any("disconnect" in p.get("kind", "").lower() for p in seen), seen
    assert any(p.get("component") == "broker" for p in seen), seen


def test_format_trade_settled_includes_win_streak_when_active() -> None:
    """A settled win on a streak-enabled parser must surface the
    streak progress + next-stake hint in the bot DM."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "won",
        "profit": 4.25,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 0,
            "max_streak": 5,
            "current_win_streak": 1,
            "max_win_streak": 2,
            "next_stake_hint": 10,
        },
    }
    msg = format_trade_settled(payload)
    assert "win streak" in msg.lower()
    assert "1/2" in msg or "1 / 2" in msg
    assert "10" in msg  # next_stake_hint surfaces somewhere


def test_format_trade_settled_notes_martingale_recovery_on_loss() -> None:
    """A settled loss with martingale auto_recovery active must
    surface the next-recovery-stake hint."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "lost",
        "profit": -5.0,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 1,
            "max_streak": 2,
            "current_win_streak": 0,
            "max_win_streak": 0,
            "next_stake_hint": 10,
        },
    }
    msg = format_trade_settled(payload)
    assert "recovery" in msg.lower() or "martingale" in msg.lower()
    assert "10" in msg  # next stake


def test_format_trade_settled_no_ladder_lines_when_at_base() -> None:
    """When neither ladder is in progress (both counters at 0), the
    settled message is the unchanged one-liner — no extra noise."""
    from autotrader.services.admin_bot_notify import format_trade_settled  # noqa: PLC0415

    payload = {
        "id": 1,
        "asset": "EURUSD",
        "direction": "call",
        "duration_seconds": 60,
        "stake": 5.0,
        "trade_mode": "live",
        "status": "won",
        "profit": 4.25,
        "settled_at": "2026-05-10T05:00:00Z",
        "ladder": {
            "current_streak": 0,
            "max_streak": 5,
            "current_win_streak": 0,
            "max_win_streak": 2,
            "next_stake_hint": 5,
        },
    }
    msg = format_trade_settled(payload)
    assert "win streak" not in msg.lower()
    assert "recovery" not in msg.lower()
