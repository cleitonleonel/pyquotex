"""Risk module tests — martingale runtime + daily caps + concurrency.

Async tests so the executor's ``wait_for_order_close`` watcher and the
HTTP calls share the same event loop. ``TestClient`` would put each
``_run(coro)`` on a fresh loop and the watcher tasks would die before
they could tick the martingale state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest

from tests.test_broker import FakeQuotex

# ---------------------------------------------------------------------------
# A FakeQuotex that lets tests dial the next ``wait_for_order_close``
# outcome explicitly so the martingale watcher gets predictable input.
# ---------------------------------------------------------------------------


class WatcherFakeQuotex(FakeQuotex):
    """FakeQuotex with an outcome queue + tiny settle delay."""

    next_outcomes: ClassVar[list[tuple[str, float]]] = []

    async def wait_for_order_close(  # type: ignore[override]
        self,
        order_id: str | int,
        duration: int = 0,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> tuple[str, float]:
        _ = (order_id, duration, timeout)
        await asyncio.sleep(0.01)
        if WatcherFakeQuotex.next_outcomes:
            return WatcherFakeQuotex.next_outcomes.pop(0)
        return "win", 0.85


@pytest.fixture(autouse=True)
def _reset_watcher_state() -> None:
    # FakeQuotex.buy / open_pending append to the *parent* class's
    # lists; resetting only the subclass's attribute leaves the
    # shared mutable state from earlier tests in place.
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []
    WatcherFakeQuotex.next_outcomes = []


@pytest.fixture
async def async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Run lifespan + HTTP + dispatches all on one loop.

    httpx.ASGITransport doesn't run the FastAPI lifespan automatically,
    so we drive it manually via ``app.router.lifespan_context``.
    """
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        WatcherFakeQuotex,
    )

    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),  # type: ignore[arg-type]
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        try:
            yield client
        finally:
            await _wipe_db(AsyncSessionLocal, app)
            app.state.pipeline.invalidate_all()
            await engine.dispose()


async def _wipe_db(session_factory, app) -> None:
    from sqlmodel import delete  # noqa: PLC0415

    from autotrader.models.broker_credentials import BrokerCredentials  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    manager = app.state.quotex_manager
    await manager.cancel_connect()
    if manager.connected:
        await manager.disconnect()
    manager.clear_credentials()
    async with session_factory() as s:
        for model in (
            BrokerCredentials,
            ParserConfig,
            TradeAttempt,
            WatchedChannel,
            GlobalSettings,
            MartingaleState,
        ):
            await s.exec(delete(model))  # type: ignore[call-overload]
        await s.commit()


# ---------------------------------------------------------------------------
# Helpers (async)
# ---------------------------------------------------------------------------


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"passcode": "test-passcode"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _connect_broker(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    await client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    await client.post("/broker/connect", headers=headers)


async def _add_watch(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    chat_id: int,
) -> None:
    await client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": chat_id,
            "title": "Signals",
            "chat_type": "channel",
            "username": "s",
            "enabled": True,
        },
    )


async def _create_parser(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    chat_id: int,
    martingale: dict | None = None,
    default_stake: float = 5.0,
    trade_mode: str = "live",
    template: str = "{DIRECTION} {ASSET} {DURATION}",
) -> int:
    body = {
        "chat_id": chat_id,
        "name": "test",
        "priority": 100,
        "parser_type": "template",
        "parser_config": {"template": template},
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {},
        "default_stake": default_stake,
        "default_duration_seconds": 60,
        "trade_mode": trade_mode,
        "aggregate_window_seconds": 0,
        "martingale": martingale
        or {
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
        },
        "enabled": True,
    }
    r = await client.post("/parsers/configs", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _activate() -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        row = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
        row.pipeline_active = True
        s.add(row)
        await s.commit()


async def _set_settings(**kwargs: float | int | bool) -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        row = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
        for k, v in kwargs.items():
            setattr(row, k, v)
        s.add(row)
        await s.commit()


async def _dispatch(
    _client: httpx.AsyncClient,
    *,
    chat_id: int,
    text: str,
) -> None:
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415

    pipeline = app.state.pipeline
    await pipeline.dispatch(
        RawMessage(
            text=text,
            chat_id=chat_id,
            sender_id=200,
            received_at=datetime.now(UTC),
        ),
    )


async def _settle_watchers(_client: httpx.AsyncClient) -> None:
    """Wait for the executor's in-flight watchers to finish."""
    from autotrader.main import app  # noqa: PLC0415

    executor = app.state.executor
    if not executor._watchers:
        return
    await asyncio.gather(
        *list(executor._watchers),
        return_exceptions=True,
    )


# ===========================================================================
# Martingale runtime
# ===========================================================================


async def test_martingale_doubles_stake_on_loss(
    async_client: httpx.AsyncClient,
) -> None:
    """Loss -> next stake = base * multiplier; win -> reset to base."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
        },
        default_stake=10.0,
    )
    await _activate()

    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[0]["amount"] == 10.0

    WatcherFakeQuotex.next_outcomes = [("win", 20.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[1]["amount"] == 20.0

    WatcherFakeQuotex.next_outcomes = [("win", 9.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    assert WatcherFakeQuotex.buy_calls[2]["amount"] == 10.0


async def test_martingale_caps_at_max_streak(
    async_client: httpx.AsyncClient,
) -> None:
    """After ``max_streak`` consecutive losses the ladder resets to base."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 2,
            "reset_on_win": True,
        },
        default_stake=10.0,
    )
    await _activate()

    for outcome in ("loss", "loss", "loss"):
        WatcherFakeQuotex.next_outcomes = [(outcome, -1.0)]
        await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
        await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [10.0, 20.0, 10.0]


async def test_martingale_disabled_uses_flat_stake(
    async_client: httpx.AsyncClient,
) -> None:
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(async_client, headers, chat_id=-1001, default_stake=7.5)
    await _activate()

    for _ in range(3):
        WatcherFakeQuotex.next_outcomes = [("loss", -7.5)]
        await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
        await _settle_watchers(async_client)

    assert all(c["amount"] == 7.5 for c in WatcherFakeQuotex.buy_calls)


# ===========================================================================
# Daily caps
# ===========================================================================


async def test_daily_max_loss_blocks_further_trades(
    async_client: httpx.AsyncClient,
) -> None:
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(async_client, headers, chat_id=-1001, default_stake=20.0)
    await _activate()
    await _set_settings(daily_max_loss=15.0)

    WatcherFakeQuotex.next_outcomes = [("loss", -20.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    assert len(WatcherFakeQuotex.buy_calls) == 1

    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    rejected = [r for r in rows if r["status"] == "rejected"]
    assert len(rejected) == 1
    assert "daily loss" in rejected[0]["error"].lower()


async def test_daily_max_stake_blocks_further_trades(
    async_client: httpx.AsyncClient,
) -> None:
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(async_client, headers, chat_id=-1001, default_stake=10.0)
    await _activate()
    await _set_settings(daily_max_stake=15.0)

    WatcherFakeQuotex.next_outcomes = [("win", 9.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    # First trade: 10 committed; second would push to 20 > cap.
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    assert len(WatcherFakeQuotex.buy_calls) == 1
    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    assert any("daily stake" in (r["error"] or "").lower() for r in rows)


# ===========================================================================
# Scheduled trades: martingale + pending-ticket capture
# ===========================================================================


async def test_scheduled_trade_advances_martingale(
    async_client: httpx.AsyncClient,
) -> None:
    """A scheduled trade's outcome should tick the streak too.

    Pre-fix bug: ``_place`` only spawned a result watcher for
    ``trade_mode == "live"`` so scheduled trades stayed ``pending``
    forever — the streak never advanced and the next signal kept
    using base stake.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    parser_id = await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        trade_mode="auto",
        template="{DIRECTION} {ASSET} {DURATION} at {TIME}",
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
        },
        default_stake=10.0,
    )
    await _activate()

    # Loss → streak should tick to 1, last_stake=10.
    fire_at = (datetime.now(UTC) + timedelta(minutes=2)).strftime("%H:%M")
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(
        async_client, chat_id=-1001, text=f"BUY EURUSD 1m at {fire_at}",
    )
    await _settle_watchers(async_client)

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        state = await s.get(MartingaleState, parser_id)
    assert state is not None
    assert state.current_streak == 1
    assert state.last_outcome == "lost"
    assert state.last_stake == 10.0

    # Next scheduled signal should be sized at 10 * 2^1 = 20.
    fire_at = (datetime.now(UTC) + timedelta(minutes=3)).strftime("%H:%M")
    WatcherFakeQuotex.next_outcomes = [("win", 19.0)]
    await _dispatch(
        async_client, chat_id=-1001, text=f"BUY EURUSD 1m at {fire_at}",
    )
    await _settle_watchers(async_client)

    assert WatcherFakeQuotex.pending_calls[-1]["amount"] == 20.0
    assert WatcherFakeQuotex.buy_calls == []  # nothing fired live


async def test_scheduled_trade_persists_pending_ticket(
    async_client: httpx.AsyncClient,
) -> None:
    """``broker_order_id`` for a scheduled trade should be the ticket.

    Pre-fix bug: ``_extract_order_id`` ran on the bool returned by
    ``open_pending`` and stored the literal string ``"True"`` as the
    broker order id. The fix reads ``client.api.pending_id`` instead.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        trade_mode="auto",
        template="{DIRECTION} {ASSET} {DURATION} at {TIME}",
        default_stake=5.0,
    )
    await _activate()

    fire_at = (datetime.now(UTC) + timedelta(minutes=2)).strftime("%H:%M")
    await _dispatch(
        async_client, chat_id=-1001, text=f"BUY EURUSD 1m at {fire_at}",
    )
    await _settle_watchers(async_client)

    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    assert len(rows) == 1
    assert rows[0]["trade_mode"] == "scheduled"
    # FakeQuotex.open_pending writes ``pending-1`` to api.pending_id.
    assert rows[0]["broker_order_id"] == "pending-1"
    assert rows[0]["broker_order_id"] != "True"
