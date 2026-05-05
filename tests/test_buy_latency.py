"""Latency-focused tests for the buy + result path optimisations.

These do NOT touch a real broker — the websocket sender is stubbed and
control-message events are pumped directly into ``QuotexAPI`` so we can
assert on:

* the exact wire frames emitted per buy (no duplicate ``settings/apply``,
  no pre-order ``tick``, no re-subscribe on the second buy of the same
  asset);
* event-driven wakeup latency for ``start_realtime_price`` and
  ``check_win`` (must be milliseconds, not the previous 200ms poll
  cycle).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.global_value import AuthStatus, WebsocketStatus
from pyquotex.stable_api import Quotex
from pyquotex.utils.account_type import AccountType


def _decode(frame: str) -> tuple[str, dict | list | None]:
    """Pull the event name + (optional) payload out of a Socket.IO frame."""
    assert frame.startswith("42[")
    body = json.loads(frame[2:])
    if isinstance(body, list) and len(body) == 1:
        return body[0], None
    assert isinstance(body, list) and len(body) == 2
    return body[0], body[1]


def _build_client(sent: list[str]) -> Quotex:
    """Build a Quotex with a stubbed websocket layer."""
    client = Quotex(email="a@b.com", password="x", auto_reconnect=False)
    client.api = QuotexAPI(
        "qxbroker.com", "a@b.com", "x", "en",
        reconnect_policy=client.reconnect_policy,
    )
    client.api._client_ref = client

    async def fake_send(data: str) -> None:
        sent.append(data)

    client.api.send_websocket_request = fake_send
    client.api.account_type = AccountType.DEMO
    client.api.tournament_id = 0
    # Fake "connected + authenticated" so check_connect() returns True.
    client.api.state.status = WebsocketStatus.CONNECTED
    client.api.state.auth_status = AuthStatus.AUTHENTICATED
    return client


@pytest.mark.asyncio
async def test_buy_emits_no_duplicate_settings_or_extra_tick():
    """A single buy() must send exactly: settings/apply + orders/open.

    Pre-fix, the wire saw: settings/apply, settings/apply (dup),
    tick (redundant), orders/open. Verify the dup + tick are gone.
    """
    sent: list[str] = []
    client = _build_client(sent)

    # Pre-populate the price cache so start_realtime_price is a no-op.
    client.api.realtime_price["EURUSD"] = [{"time": 1, "price": 1.1}]

    # Drive buy() concurrently and feed the buy_confirmed event so it
    # returns. We can't await client.buy() directly because it'll block
    # on the event registry; spawn it as a task and pump the event.
    buy_task = asyncio.create_task(
        client.buy(amount=10, asset="EURUSD", direction="call", duration=60)
    )
    await asyncio.sleep(0)  # let the task run up to the wait_event
    await asyncio.sleep(0)
    await client.api.event_registry.set_event(
        'buy_confirmed', {"id": "abc", "asset": "EURUSD"}
    )
    ok, data = await asyncio.wait_for(buy_task, timeout=5)

    assert ok, f"buy() failed: {data}"
    events = [_decode(f)[0] for f in sent]
    assert events.count("settings/apply") == 1, events
    assert "tick" not in events, events
    assert events[-1] == "orders/open"


@pytest.mark.asyncio
async def test_repeated_buy_skips_re_subscribe():
    """Second buy() on the same asset must NOT re-issue the 3 subscribe
    sends — start_candles_stream short-circuits via _subscribed_assets."""
    sent: list[str] = []
    client = _build_client(sent)
    client.api.realtime_price["EURUSD"] = [{"time": 1, "price": 1.1}]

    async def run_one_buy() -> None:
        task = asyncio.create_task(
            client.buy(amount=10, asset="EURUSD", direction="call", duration=60)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await client.api.event_registry.set_event(
            'buy_confirmed', {"id": "abc", "asset": "EURUSD"}
        )
        await asyncio.wait_for(task, timeout=5)

    await run_one_buy()
    first_run_frames = list(sent)

    sent.clear()
    await client.api.event_registry.clear_event('buy_confirmed')
    await run_one_buy()
    second_run_frames = list(sent)

    first_events = [_decode(f)[0] for f in first_run_frames]
    second_events = [_decode(f)[0] for f in second_run_frames]

    # First buy issued the subscribe trio (subfor + chart + depth-follow).
    assert "instruments/update" in first_events or any(
        "subscribe" in e for e in first_events
    ), first_events
    # Second buy must NOT issue any of those subscribe events.
    for e in second_events:
        assert "subscribe" not in e and "chart_notification/get" not in e, (
            f"unexpected re-subscribe on second buy: {e}"
        )


@pytest.mark.asyncio
async def test_check_win_is_event_driven():
    """check_win must wake within milliseconds of order_closed firing,
    not after a 200ms poll cycle."""
    sent: list[str] = []
    client = _build_client(sent)

    order_id = "xyz123"
    # Pre-fire the close: listinfodata populated as the WS branch does.
    async def deliver_close() -> None:
        await asyncio.sleep(0.005)  # a few ms — far less than the old 200ms poll
        client.api.listinfodata.set("win", 1, str(order_id), 8.5)
        client.api.listinfodata.set("win", 1, order_id, 8.5)
        await client.api.event_registry.set_event(
            f'order_closed_{order_id}', {"id": order_id, "profit": 8.5}
        )

    asyncio.create_task(deliver_close())
    t0 = time.perf_counter()
    status, profit = await client.check_win(order_id, duration=60)
    elapsed = time.perf_counter() - t0

    assert status == "win"
    assert profit == 8.5
    # The previous polling impl floored at 200ms; we must be well below.
    assert elapsed < 0.1, f"check_win took {elapsed:.3f}s (expected <0.1s)"


@pytest.mark.asyncio
async def test_check_win_fast_path_when_already_closed():
    sent: list[str] = []
    client = _build_client(sent)
    order_id = "fast"
    client.api.listinfodata.set("win", 1, str(order_id), 5.0)
    client.api.listinfodata.set("win", 1, order_id, 5.0)

    t0 = time.perf_counter()
    status, profit = await client.check_win(order_id, duration=60)
    elapsed = time.perf_counter() - t0

    assert status == "win"
    assert profit == 5.0
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_wait_for_order_close_is_alias_of_check_win():
    sent: list[str] = []
    client = _build_client(sent)
    order_id = "alias"
    client.api.listinfodata.set("loss", 1, str(order_id), -2.0)
    client.api.listinfodata.set("loss", 1, order_id, -2.0)

    status, profit = await client.wait_for_order_close(order_id, duration=60)
    assert status == "loss"
    assert profit == -2.0


@pytest.mark.asyncio
async def test_start_realtime_price_event_driven():
    """The new event path must wake on price_ready_{asset} without
    waiting the old 200ms poll cycle."""
    sent: list[str] = []
    client = _build_client(sent)

    async def deliver_first_tick() -> None:
        await asyncio.sleep(0.005)
        # Mirror what api._on_message does on the first tick.
        client.api.realtime_price["NEWPAIR"].append({"time": 1, "price": 1.0})
        await client.api.event_registry.set_event(
            'price_ready_NEWPAIR', ["NEWPAIR", 1, 1.0, 0]
        )

    asyncio.create_task(deliver_first_tick())
    t0 = time.perf_counter()
    result = await client.start_realtime_price("NEWPAIR", period=60, timeout=2)
    elapsed = time.perf_counter() - t0

    assert "NEWPAIR" in result
    assert elapsed < 0.1, f"start_realtime_price took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_start_realtime_price_fast_path_when_cached():
    sent: list[str] = []
    client = _build_client(sent)
    client.api.realtime_price["EURUSD"] = [{"time": 1, "price": 1.1}]

    t0 = time.perf_counter()
    result = await client.start_realtime_price("EURUSD", period=60)
    elapsed = time.perf_counter() - t0

    assert "EURUSD" in result
    # No event wait; first call may still issue subscribe sends but
    # they're awaited only on the WS stub which is instant.
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_start_candles_stream_caches_subscriptions():
    sent: list[str] = []
    client = _build_client(sent)

    await client.start_candles_stream("EURUSD", 60)
    after_first = len(sent)
    await client.start_candles_stream("EURUSD", 60)
    after_second = len(sent)

    # Second call must not have issued any new frames.
    assert after_second == after_first

    # A different period IS a different cache key — must subscribe again.
    await client.start_candles_stream("EURUSD", 30)
    after_third = len(sent)
    assert after_third > after_second


@pytest.mark.asyncio
async def test_buy_confirm_timeout_short():
    """When buy_confirmed never fires, buy() must return within ~10s
    (the new confirm_timeout default), not duration+5 seconds."""
    sent: list[str] = []
    client = _build_client(sent)
    client.api.realtime_price["EURUSD"] = [{"time": 1, "price": 1.1}]

    t0 = time.perf_counter()
    ok, msg = await client.buy(
        amount=10, asset="EURUSD", direction="call", duration=300,
        confirm_timeout=0.1,
    )
    elapsed = time.perf_counter() - t0

    assert ok is False
    assert msg == "Timeout"
    assert elapsed < 1.0, (
        f"buy timed out after {elapsed:.3f}s — confirm_timeout not honoured"
    )
