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


@pytest.mark.parametrize(
    "value,expected",
    [
        (5.0, 5),
        (5.5, 6),
        (9.25, 10),       # base $5 + 85% payout
        (10.0, 10),
        (18.50, 19),      # $10 base + 85%
        (37.0, 37),
        (37.01, 38),      # ceiling, never round-down
        (0.0, 0),
        (0.99, 1),
    ],
)
def test_round_stake_ceils_to_int_for_quotex(value: float, expected: int) -> None:
    """Quotex's stake field is integer-only; rounding-up is the
    correct safety direction (never under-stake the operator's
    intended risk). Helper test stays pure / synchronous so it runs
    fast and shows up high in the pytest report."""
    from autotrader.services.risk_gate import _round_stake  # noqa: PLC0415

    assert _round_stake(value) == expected


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


def _disable_parser_payload(cfg_id: int) -> dict[str, object]:
    """Minimal PUT body to flip enabled=False without changing other
    fields. Mirrors the ConfigPayload shape from routers/parsers.py."""
    _ = cfg_id
    return {
        "name": "p",
        "priority": 100,
        "parser_type": "template",
        "parser_config": {"template": "{DIRECTION} {ASSET}"},
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {},
        "aggregate_window_seconds": 0,
        "default_stake": 10.0,
        "default_duration_seconds": 60,
        "trade_mode": "live",
        "martingale": {
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 3,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        "enabled": False,
    }


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
    """``max_streak=N`` allows N recovery steps before the ladder
    resets to base. With ``max_streak=2`` we expect base, then the
    1st recovery (×mult), then the 2nd recovery (×mult²), then a
    reset back to base on the 4th loss in the streak."""
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

    for outcome in ("loss", "loss", "loss", "loss"):
        WatcherFakeQuotex.next_outcomes = [(outcome, -1.0)]
        await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
        await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    # base, ×2 (recovery 1), ×4 (recovery 2), reset to base.
    assert amounts == [10.0, 20.0, 40.0, 10.0]


async def test_martingale_auto_recovery_fires_same_dir_after_loss(
    async_client: httpx.AsyncClient,
) -> None:
    """``auto_recovery=True`` makes a *losing* trade trigger an
    immediate same-asset / same-direction trade with the multiplied
    stake — without waiting for the channel to send another signal.

    Channels phrase their guidance as *"IF LOSS TAKE 1 STEP MTG (Same
    Direction Double Amount)"* — the channel does not repost the
    signal, the bot is expected to fire the recovery itself. This
    test pins that behaviour so a refactor of ``_settle_one`` can't
    quietly drop the auto-recovery hook."""
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
            "auto_recovery": True,
        },
        default_stake=10.0,
    )
    await _activate()

    # Original signal loses; auto-recovery wins on the next slot.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0), ("win", 18.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    # Drain the original watcher; the recovery's own watcher will be
    # registered *during* settlement, so we settle a second time.
    await _settle_watchers(async_client)
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    # Original $10 (base, step 0). After loss → recovery fires at $20.
    assert amounts == [10.0, 20.0], amounts

    # Sanity: every buy_call on the recovery uses the original's
    # asset/direction shape — auto-recovery doesn't invent a new
    # asset, it follows the lost trade's footprint.
    assert WatcherFakeQuotex.buy_calls[0]["asset"] == \
        WatcherFakeQuotex.buy_calls[1]["asset"]
    assert WatcherFakeQuotex.buy_calls[0]["direction"] == \
        WatcherFakeQuotex.buy_calls[1]["direction"]


async def test_martingale_auto_recovery_off_falls_back_to_stake_mul_only(
    async_client: httpx.AsyncClient,
) -> None:
    """``auto_recovery=False`` (the default) preserves the legacy
    "multiply the next *channel* signal's stake" behaviour. A loss
    must NOT trigger an autonomous trade."""
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
            "auto_recovery": False,
        },
        default_stake=10.0,
    )
    await _activate()

    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)
    # Drain a second time in case auto-recovery was incorrectly
    # wired — anything queued here would be the regression we guard.
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [10.0], (
        "auto_recovery=False must NOT fire a recovery trade — only "
        "the next dispatched channel signal should see the multiplied "
        f"stake. Got buy_calls: {amounts}"
    )


async def test_martingale_max_streak_one_takes_one_recovery_step(
    async_client: httpx.AsyncClient,
) -> None:
    """Regression for the ``max_streak=1`` no-op bug. Channels phrase
    their guidance as *"TAKE 1 STEP MTG"* and operators set
    ``max_streak=1`` expecting one recovery trade after a loss. Earlier
    revisions used a ``>=`` reset comparator, which incremented the
    streak to 1 and reset it on the *same* settled-trade callback —
    so the next trade always read ``current_streak=0`` and the
    martingale never fired. This test pins the corrected ``>``
    comparator."""
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
            "max_streak": 1,
            "reset_on_win": True,
        },
        default_stake=10.0,
    )
    await _activate()

    for outcome in ("loss", "loss", "loss", "loss"):
        WatcherFakeQuotex.next_outcomes = [(outcome, -1.0)]
        await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")
        await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    # base → 1 recovery (×2) → reset → base → 1 recovery (×2).
    assert amounts == [10.0, 20.0, 10.0, 20.0]


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


# ===========================================================================
# Startup reconciler — clears stuck ``pending`` rows from prior process
# ===========================================================================


async def _insert_pending(
    *,
    broker_order_id: str | None,
    placed_at: datetime | None = None,
    placed_at_none: bool = False,
    fire_at: datetime | None = None,
    trade_mode: str = "scheduled",
    duration_seconds: int = 60,
) -> int:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    resolved_placed_at = (
        None if placed_at_none else (placed_at or datetime.now(UTC))
    )
    row = TradeAttempt(
        chat_id=-1001,
        parser_config_id=1,
        asset="EURUSD",
        asset_raw="EUR/USD",
        direction="call",
        duration_seconds=duration_seconds,
        stake=1.0,
        trade_mode=trade_mode,
        fire_at=fire_at,
        status="pending",
        broker_order_id=broker_order_id,
        placed_at=resolved_placed_at,
    )
    async with AsyncSessionLocal() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row.id or 0


async def test_reconcile_buckets_pending_rows_three_ways(
    async_client: httpx.AsyncClient,
) -> None:
    """Pending rows at startup land in one of three buckets.

    * ``placed_at is None`` -> expire immediately (broker never got
      the order); legacy "watcher lost on restart" note.
    * ``placed_at + duration + slack <= now`` -> expire immediately
      with the clearer "settle window passed" note.
    * ``placed_at + duration + slack > now`` -> stay pending; a
      deferred task will mark it expired once the natural window
      closes.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)

    # Bucket 1: placed_at None — broker never accepted.
    never_placed_id = await _insert_pending(
        broker_order_id=None,
        placed_at_none=True,
    )
    # Bucket 2: settle window long past.
    past_id = await _insert_pending(
        broker_order_id="real-ticket-1",
        placed_at=datetime.now(UTC) - timedelta(hours=1),
        trade_mode="live",
    )
    # Bucket 3: still in flight (placed just now, 60s window + 60s slack).
    inflight_id = await _insert_pending(
        broker_order_id="real-ticket-2",
        placed_at=datetime.now(UTC),
        fire_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    from autotrader.main import app  # noqa: PLC0415

    await app.state.executor.reconcile_pending()

    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    by_id = {r["id"]: r for r in rows}

    never_row = by_id[never_placed_id]
    assert never_row["status"] == "expired"
    assert "watcher lost on restart" in (never_row["error"] or "").lower()

    past_row = by_id[past_id]
    assert past_row["status"] == "expired"
    assert "settle window passed" in (past_row["error"] or "").lower()

    inflight_row = by_id[inflight_id]
    assert inflight_row["status"] == "pending", (
        f"in-flight row must stay pending; got "
        f"status={inflight_row['status']!r}, error={inflight_row['error']!r}"
    )


async def test_reconcile_is_idempotent(
    async_client: httpx.AsyncClient,
) -> None:
    """Calling reconcile twice does no harm — already-expired rows stay put."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)

    # Use a placed_at in the past so the row falls into the immediate-
    # expire bucket — keeps the assertion deterministic.
    attempt_id = await _insert_pending(
        broker_order_id="True",
        placed_at=datetime.now(UTC) - timedelta(hours=1),
    )

    from autotrader.main import app  # noqa: PLC0415

    await app.state.executor.reconcile_pending()
    await app.state.executor.reconcile_pending()

    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    expired = [r for r in rows if r["id"] == attempt_id]
    assert len(expired) == 1
    assert expired[0]["status"] == "expired"


async def test_watcher_marks_expired_when_broker_disconnected(
    async_client: httpx.AsyncClient,
) -> None:
    """A watcher firing with a torn-down broker shouldn't leave a pending row."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)

    attempt_id = await _insert_pending(broker_order_id="real-ticket-3")

    from autotrader.main import app  # noqa: PLC0415

    # Tear down the broker connection so the watcher hits the
    # ``client is None`` branch.
    await app.state.quotex_manager.disconnect()
    await app.state.executor._watch_result(  # type: ignore[attr-defined]
        attempt_id, "real-ticket-3", duration=60,
    )

    rows = (await async_client.get("/pipeline/trades", headers=headers)).json()
    row = next(r for r in rows if r["id"] == attempt_id)
    assert row["status"] == "expired"
    assert "broker disconnected" in (row["error"] or "").lower()


async def test_martingale_auto_recovery_skips_when_parser_disabled_mid_streak(
    async_client: httpx.AsyncClient,
) -> None:
    """If the operator disables the parser between the loss settle
    and the recovery dispatch, the recovery must NOT fire — the
    user has explicitly told the bot to stop trading on this parser.
    Today the recovery path uses a stale ``cfg`` snapshot captured
    at submit time and never refetches; this test pins the fix that
    re-reads the row inside ``_fire_auto_recovery`` and bails on
    ``enabled=False``."""
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, -1001)
    cfg_id = await _create_parser(
        async_client,
        headers,
        chat_id=-1001,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 3,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        default_stake=10.0,
    )
    await _activate()

    # Original signal loses; before the recovery fires, disable the
    # parser. The settle path runs synchronously inside _settle_watchers
    # so we disable BEFORE that drain.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _dispatch(async_client, chat_id=-1001, text="BUY EURUSD 1m")

    # Disable the parser. The original trade is still pending in
    # WatcherFakeQuotex's queue — its settle will then attempt to
    # fire a recovery that should now be blocked.
    r = await async_client.put(
        f"/parsers/configs/{cfg_id}",
        headers=headers,
        json=_disable_parser_payload(cfg_id),
    )
    assert r.status_code == 200, r.text

    await _settle_watchers(async_client)
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [10.0], (
        "auto_recovery must skip when the parser was disabled mid-streak; "
        f"got buy_calls={amounts}"
    )

    # The refactor short-circuits BEFORE risk_gate.evaluate runs,
    # so no phantom 'rejected' TradeAttempt row should exist for
    # the recovery. Without the refactor, _fire_auto_recovery would
    # have entered submit() and written a rejected row before
    # risk_gate blocked. With the refactor, it bails BEFORE submit()
    # so the only row in the DB is the original (settled lost).
    from sqlmodel import select  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    async def _count_rejected() -> int:
        async with AsyncSessionLocal() as s:
            result = await s.exec(
                select(TradeAttempt).where(TradeAttempt.status == "rejected"),
            )
            return len(list(result.all()))

    rejected_count = await _count_rejected()
    assert rejected_count == 0, (
        "_fire_auto_recovery must short-circuit BEFORE submit() so no "
        "rejected TradeAttempt is written for the recovery; "
        f"got {rejected_count} rejected rows"
    )
