"""End-to-end integration tests that exercise the real ``Quotex`` client
against a local :class:`WSReplayServer`.

These tests prove the WS stack works (socket.io framing, dispatch table,
slot registry, subscription tracking, reconnect loop) without ever
touching the broker. They run on CI.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

import pytest

from pyquotex.global_value import AuthStatus, WebsocketStatus
from pyquotex.stable_api import Quotex
from pyquotex.types import ReconnectPolicy
from tests.fakes.ws_replay_server import (
    WSReplayServer,
    candle_history_frames,
)

# Default replay greeting handles auth + an empty instruments/list, so a
# bare connect() finishes within a few hundred ms.


@pytest.mark.integration
@pytest.mark.asyncio
async def test_connect_completes_against_replay(
    offline_quotex: Callable[..., Quotex],
) -> None:
    client = offline_quotex()
    ok, reason = await client.connect()
    try:
        assert ok, reason
        assert client.api is not None
        assert client.api.state.status == WebsocketStatus.CONNECTED
        assert client.api.state.auth_status == AuthStatus.AUTHENTICATED
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_handles_balance_event(
    offline_quotex: Callable[..., Quotex],
) -> None:
    """The default greeting includes a balance frame — assert it landed."""
    client = offline_quotex()
    ok, _ = await client.connect()
    assert ok
    try:
        # Wait briefly for the greeting frames to be processed.
        for _ in range(50):
            if client.api and client.api.account_balance is not None:
                break
            await asyncio.sleep(0.02)
        assert client.api is not None
        assert client.api.account_balance == {
            "demoBalance": 10000.0,
            "liveBalance": 0.0,
            "currencyCode": "USD",
        }
        assert client.api.slots.balance.is_set()
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_balance_returns_event_driven_value(
    offline_quotex: Callable[..., Quotex],
) -> None:
    client = offline_quotex()
    await client.connect()
    try:
        balance = await client.get_balance(timeout=2)
        # demoBalance + profit_in_operation (None → 0). truncate to 2dp.
        assert balance == 10000.0
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dispatch_handles_instruments_placeholder(
    offline_quotex: Callable[..., Quotex],
) -> None:
    """Greeting ends with the placeholder pattern + payload — instruments fill."""
    client = offline_quotex()
    await client.connect()
    try:
        for _ in range(100):
            if client.api and client.api.instruments:
                break
            await asyncio.sleep(0.02)
        assert client.api is not None
        assert client.api.instruments
        assert client.api.instruments[0][1] == "EURUSD"
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_history_load_returns_candles(
    offline_quotex: Callable[..., Quotex],
    replay_server: WSReplayServer,
) -> None:
    """Script a candle response and verify ``get_candles`` parses it."""
    replay_server.on_event(
        "history/load",
        reply=candle_history_frames(asset="EURUSD", period=60, n=30),
    )
    # Stubs for subscribe / chart / follow — just ACK silently.
    for event in (
        "instruments/update",
        "chart_notification/get",
        "depth/follow",
    ):
        replay_server.on_event(event, reply=[])

    client = offline_quotex()
    await client.connect()
    try:
        candles = await client.get_candles("EURUSD", None, 1800, 60, timeout=5)
        assert candles is not None
        assert len(candles) > 0
        assert "close" in candles[0]
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_candles_use_cache_hits_on_second_call(
    offline_quotex: Callable[..., Quotex],
    replay_server: WSReplayServer,
) -> None:
    """Second call with use_cache=True should not round-trip to the WS."""
    replay_server.on_event(
        "history/load",
        reply=candle_history_frames(asset="EURUSD", period=60, n=30),
    )
    for event in (
        "instruments/update",
        "chart_notification/get",
        "depth/follow",
    ):
        replay_server.on_event(event, reply=[])

    client = offline_quotex()
    await client.connect()
    try:
        # Pick an end_from_time that falls in a stable bucket.
        end_time = 1_700_000_000.0

        t0 = time.monotonic()
        first = await client.get_candles(
            "EURUSD", end_time, 1800, 60, use_cache=True, timeout=5
        )
        elapsed_first = time.monotonic() - t0
        assert first is not None and len(first) > 0

        baseline_history_loads = sum(
            1 for m in replay_server.received if '"history/load"' in m
        )

        t0 = time.monotonic()
        second = await client.get_candles(
            "EURUSD", end_time, 1800, 60, use_cache=True, timeout=5
        )
        elapsed_second = time.monotonic() - t0
        assert second == first
        # No second history/load went over the wire.
        new_history_loads = sum(
            1 for m in replay_server.received if '"history/load"' in m
        )
        assert new_history_loads == baseline_history_loads
        # And the cached call is at least ~10x faster.
        assert elapsed_second < elapsed_first / 5
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_subscription_tracked_after_start_candles_stream(
    offline_quotex: Callable[..., Quotex],
    replay_server: WSReplayServer,
) -> None:
    for event in (
        "instruments/update",
        "chart_notification/get",
        "depth/follow",
    ):
        replay_server.on_event(event, reply=[])

    client = offline_quotex()
    await client.connect()
    try:
        await client.start_candles_stream("EURUSD", 60)
        assert client.api is not None
        assert "candle:EURUSD:60" in client.api._subscriptions
        sub = client.api._subscriptions["candle:EURUSD:60"]
        assert sub.kind == "candle"
        assert sub.asset == "EURUSD"
        assert sub.period == 60
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconnect_replays_subscriptions_against_replay(
    offline_quotex: Callable[..., Quotex],
    replay_server: WSReplayServer,
) -> None:
    """End-to-end: open candle stream, force the WS down, watch replay happen."""
    for event in (
        "instruments/update",
        "chart_notification/get",
        "depth/follow",
    ):
        replay_server.on_event(event, reply=[])

    client = offline_quotex(
        reconnect_policy=ReconnectPolicy(
            enabled=True,
            max_attempts=2,
            base_delay=0.05,
            max_delay=0.2,
            jitter=0.0,
            stale_timeout=0,
        ),
    )
    await client.connect()
    try:
        await client.start_candles_stream("EURUSD", 60)
        assert client.api is not None
        api = client.api
        ws_client = api.websocket_client
        assert ws_client is not None

        baseline_subs = sum(
            1 for m in replay_server.received if '"instruments/update"' in m
        )
        assert len(replay_server.connections) >= 1, "no server-side connection"
        for conn in list(replay_server.connections):
            await conn.close(code=1011, reason="forced")

        # Wait for the second open. Be generous; CI is slow.
        for _ in range(200):
            await asyncio.sleep(0.05)
            if ws_client._open_count >= 2:  # type: ignore[attr-defined]
                break

        assert ws_client._open_count >= 2, (  # type: ignore[attr-defined]
            f"Reconnect did not open a second WS (open_count="
            f"{ws_client._open_count})"  # type: ignore[attr-defined]
        )
        # Now wait for replay to fire the subscribe.
        for _ in range(100):
            await asyncio.sleep(0.05)
            new_subs = sum(
                1 for m in replay_server.received if '"instruments/update"' in m
            )
            if new_subs > baseline_subs:
                break

        new_subs = sum(
            1 for m in replay_server.received if '"instruments/update"' in m
        )
        assert new_subs > baseline_subs, (
            f"Replay did not re-issue subscribe (saw {new_subs}, "
            f"baseline {baseline_subs})"
        )
    finally:
        await client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_async_context_manager_closes_cleanly(
    offline_quotex: Callable[..., Quotex],
) -> None:
    client = offline_quotex()
    async with client as q:
        assert q.api is not None
        assert q.api.state.status == WebsocketStatus.CONNECTED
        ws_client = q.api.websocket_client
        assert ws_client is not None and ws_client.is_alive()
    # After exit, the WS layer should be closed even if the cached
    # state enum hasn't been fully drained from the dispatch loop.
    assert client.api is not None
    assert client.api.websocket_client is not None
    assert not client.api.websocket_client.is_alive()
    assert client.api.websocket_client._closing is True  # type: ignore[attr-defined]
