"""Stats + live-feed tests (Phase 6).

The stats endpoint operates on today's ``trade_attempts`` rows so the
test fixture inserts pre-built rows directly to avoid driving the
full pipeline for every scenario. The WebSocket test exercises the
auth gate and the event-bus fanout.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from autotrader.services.event_bus import TradeEventBus
from tests.test_broker import FakeQuotex


@pytest.fixture(autouse=True)
def _reset_fake_quotex_state() -> None:
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []


@pytest.fixture
async def async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
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
            await _wipe_db(AsyncSessionLocal)
            await engine.dispose()


async def _wipe_db(session_factory) -> None:
    from sqlmodel import delete  # noqa: PLC0415

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with session_factory() as s:
        for model in (TradeAttempt, ParserConfig, WatchedChannel):
            await s.exec(delete(model))  # type: ignore[call-overload]
        await s.commit()


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"passcode": "test-passcode"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _login_token(client: httpx.AsyncClient) -> str:
    r = await client.post("/auth/login", json={"passcode": "test-passcode"})
    return r.json()["token"]


async def _add_attempt(
    *,
    chat_id: int,
    status: str,
    profit: float | None = None,
    stake: float = 1.0,
    received_at: datetime | None = None,
    placed_at: datetime | None = None,
    settled_at: datetime | None = None,
) -> int:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    row = TradeAttempt(
        chat_id=chat_id,
        parser_config_id=1,
        asset="EURUSD",
        asset_raw="EUR/USD",
        direction="call",
        duration_seconds=60,
        stake=stake,
        trade_mode="live",
        status=status,
        profit=profit,
        received_at=received_at or datetime.now(UTC),
        placed_at=placed_at,
        settled_at=settled_at,
    )
    async with AsyncSessionLocal() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row.id or 0


async def _add_watch_row(
    *,
    chat_id: int,
    title: str,
    enabled: bool = True,
) -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        s.add(
            WatchedChannel(
                chat_id=chat_id,
                title=title,
                chat_type="channel",
                username=None,
                enabled=enabled,
            ),
        )
        await s.commit()


async def _add_parser_config(
    *,
    chat_id: int,
    name: str = "p",
    enabled: bool = True,
) -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        s.add(
            ParserConfig(
                chat_id=chat_id,
                name=name,
                parser_type="template",
                enabled=enabled,
            ),
        )
        await s.commit()


# ===========================================================================
# Stats overview
# ===========================================================================


async def test_stats_channel_aggregates_today(
    async_client: httpx.AsyncClient,
) -> None:
    """Per-channel breakdown buckets statuses, computes win rate and P&L."""
    headers = await _login(async_client)
    await _add_watch_row(chat_id=-1001, title="EURChannel")

    now = datetime.now(UTC)
    # Channel -1001: 2 wins, 1 loss, 1 rejected.
    await _add_attempt(chat_id=-1001, status="won", profit=0.85, stake=10.0)
    await _add_attempt(chat_id=-1001, status="won", profit=0.85, stake=10.0)
    await _add_attempt(chat_id=-1001, status="lost", profit=-10.0, stake=10.0)
    await _add_attempt(chat_id=-1001, status="rejected", stake=10.0)

    # Channel -1002: 1 broker_error, 1 expired (no title row → falls back).
    await _add_attempt(chat_id=-1002, status="broker_error", stake=5.0)
    await _add_attempt(chat_id=-1002, status="expired", stake=5.0)

    # Yesterday — must be excluded from "today_utc".
    yesterday = now - timedelta(days=2)
    await _add_attempt(
        chat_id=-1001, status="won", profit=0.85, stake=10.0,
        received_at=yesterday,
    )

    r = await async_client.get("/stats/overview", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {c["chat_id"]: c for c in body["channels"]}

    eur = by_id[-1001]
    assert eur["title"] == "EURChannel"
    assert eur["total"] == 4  # yesterday excluded
    assert eur["won"] == 2
    assert eur["lost"] == 1
    assert eur["rejected"] == 1
    assert eur["win_rate"] == pytest.approx(2 / 3)
    assert eur["realised_pnl"] == pytest.approx(0.85 + 0.85 - 10.0)
    # Committed = pending + won + lost (not rejected/broker_error/expired).
    assert eur["committed_stake"] == pytest.approx(30.0)

    other = by_id[-1002]
    assert other["title"] == "chat -1002"  # no watched-channel row
    assert other["broker_error"] == 1
    assert other["expired"] == 1
    assert other["win_rate"] is None  # no settled trades


async def test_stats_includes_armed_channels_with_no_trades(
    async_client: httpx.AsyncClient,
) -> None:
    """Watched channel + enabled parser shows up even with 0 trades today.

    Without this, an idle parser looks un-configured on the dashboard
    and the user can't tell it's actually wired up.
    """
    headers = await _login(async_client)
    await _add_watch_row(chat_id=-2001, title="IdleChannel")
    await _add_parser_config(chat_id=-2001, name="idle-parser")

    # A second channel that is watched but has no parser → must be hidden.
    await _add_watch_row(chat_id=-2002, title="Unconfigured")

    # A third channel where the parser is disabled → must be hidden.
    await _add_watch_row(chat_id=-2003, title="DisabledParser")
    await _add_parser_config(chat_id=-2003, name="off", enabled=False)

    r = await async_client.get("/stats/overview", headers=headers)
    assert r.status_code == 200, r.text
    by_id = {c["chat_id"]: c for c in r.json()["channels"]}

    assert -2001 in by_id
    idle = by_id[-2001]
    assert idle["title"] == "IdleChannel"
    assert idle["total"] == 0
    assert idle["won"] == 0
    assert idle["lost"] == 0
    assert idle["win_rate"] is None
    assert idle["realised_pnl"] == 0
    assert idle["committed_stake"] == 0

    assert -2002 not in by_id
    assert -2003 not in by_id


async def test_stats_latency_percentiles(
    async_client: httpx.AsyncClient,
) -> None:
    """Latency tiles surface p50 and p99 for both pipeline stages."""
    headers = await _login(async_client)
    base = datetime.now(UTC)

    # signal→place latencies in ms: 100, 200, 300, 400, 500, 5000
    for delay_ms in (100, 200, 300, 400, 500, 5000):
        recv = base
        placed = base + timedelta(milliseconds=delay_ms)
        await _add_attempt(
            chat_id=-1001,
            status="pending",
            received_at=recv,
            placed_at=placed,
        )

    # place→settle latencies (subset that has settled_at)
    for delay_ms in (60_000, 60_500, 61_000, 90_000):
        recv = base
        placed = base + timedelta(milliseconds=50)
        settled = placed + timedelta(milliseconds=delay_ms)
        await _add_attempt(
            chat_id=-1001,
            status="won",
            profit=0.5,
            received_at=recv,
            placed_at=placed,
            settled_at=settled,
        )

    r = await async_client.get("/stats/overview", headers=headers)
    assert r.status_code == 200
    lat = r.json()["latency"]

    sig = lat["signal_to_place"]
    assert sig["count"] == 6 + 4  # both lists contribute placed_at samples
    assert sig["p50_ms"] is not None
    assert sig["p99_ms"] is not None
    # Sanity: p99 dominates p50.
    assert sig["p99_ms"] >= sig["p50_ms"]

    settle = lat["place_to_settle"]
    assert settle["count"] == 4
    # p50 over [60_000, 60_500, 61_000, 90_000] sits between the 2nd
    # and 3rd samples.
    assert 60_400 < settle["p50_ms"] < 60_900
    # p99 close to the max.
    assert settle["p99_ms"] is not None
    assert 89_000 < settle["p99_ms"] <= 90_000


async def test_stats_handles_empty_window(
    async_client: httpx.AsyncClient,
) -> None:
    headers = await _login(async_client)
    r = await async_client.get("/stats/overview", headers=headers)
    body = r.json()
    assert body["channels"] == []
    assert body["latency"]["signal_to_place"]["count"] == 0
    assert body["latency"]["signal_to_place"]["p50_ms"] is None
    assert body["latency"]["place_to_settle"]["p50_ms"] is None


async def test_stats_requires_auth(async_client: httpx.AsyncClient) -> None:
    r = await async_client.get("/stats/overview")
    assert r.status_code in (401, 403)


# ===========================================================================
# Event bus fan-out
# ===========================================================================


async def test_event_bus_fans_out_to_subscribers() -> None:
    bus = TradeEventBus()

    async def collect(n: int) -> list[str]:
        out: list[str] = []
        async for ev in bus.subscribe():
            out.append(ev.type)
            if len(out) == n:
                break
        return out

    a = asyncio.create_task(collect(2))
    b = asyncio.create_task(collect(2))
    # Yield once so both subscribers register their queues.
    await asyncio.sleep(0)

    bus.publish("trade.upserted", {"id": 1})
    bus.publish("trade.upserted", {"id": 2})

    a_events, b_events = await asyncio.wait_for(
        asyncio.gather(a, b), timeout=1.0,
    )
    assert a_events == ["trade.upserted", "trade.upserted"]
    assert b_events == ["trade.upserted", "trade.upserted"]


async def test_event_bus_drops_when_queue_full() -> None:
    """Slow subscriber must not wedge the executor."""
    bus = TradeEventBus(max_queue=2)

    queue_count_before = bus.subscriber_count
    iterator = bus.subscribe().__aiter__()
    # Force one yield so the queue is registered.
    consume_task = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0)
    assert bus.subscriber_count == queue_count_before + 1

    bus.publish("trade.upserted", {"id": 1})
    bus.publish("trade.upserted", {"id": 2})
    bus.publish("trade.upserted", {"id": 3})  # dropped (queue saturated)
    bus.publish("trade.upserted", {"id": 4})  # dropped

    first = await asyncio.wait_for(consume_task, timeout=1.0)
    assert first.payload["id"] == 1


# ===========================================================================
# WebSocket trade feed
# ===========================================================================


async def test_feed_ws_rejects_missing_token(
    async_client: httpx.AsyncClient,
) -> None:
    """No ``?token=`` → policy-violation close before any frame.

    Streaming behaviour itself is covered by the event-bus fanout
    tests above; the WS handler is a thin wrapper that just chains
    ``bus.subscribe()`` to ``websocket.send_json``. Driving the full
    WS through Starlette's sync ``TestClient`` opens a second
    lifespan in a thread, which forks ``app.state`` and makes
    cross-thread publish/subscribe non-deterministic — not worth
    re-validating the same chain here.
    """
    from starlette.testclient import TestClient as StarletteTestClient  # noqa: PLC0415
    from starlette.websockets import WebSocketDisconnect  # noqa: PLC0415

    from autotrader.main import app  # noqa: PLC0415

    with (
        StarletteTestClient(app) as sync_client,
        pytest.raises(WebSocketDisconnect),
        sync_client.websocket_connect("/feed/ws"),
    ):
        pass
