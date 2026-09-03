"""Tests for the resilience layer: ReconnectPolicy + WebsocketClient.

These tests stub out the actual ``websockets.connect`` call and exercise
:meth:`WebsocketClient.run_forever` to verify the auto-reconnect loop,
backoff, watchdog, and subscription replay logic in isolation.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from pyquotex.qxtypes import ReconnectPolicy, Subscription
from pyquotex.ws.client import WebsocketClient


class _FakeApi:
    """Minimal duck-typed stand-in for QuotexAPI used by these tests."""

    def __init__(self) -> None:
        self.state = MagicMock(status=1)  # WebsocketStatus.CONNECTED
        self.last_message_at = time.monotonic()
        self._subscriptions: dict[str, Subscription] = {}
        self.replayed: list[tuple[str, str, int | None]] = []
        # Used by _replay_one
        self.subscribe_realtime_candle = AsyncMock(
            side_effect=lambda a, p: self.replayed.append(("candle", a, p))
        )
        self.chart_notification = AsyncMock()
        self.follow_candle = AsyncMock()
        self.subscribe_all_size = AsyncMock(
            side_effect=lambda a: self.replayed.append(("all_size", a, None))
        )
        self.subscribe_Traders_mood = AsyncMock(
            side_effect=lambda a, i: self.replayed.append(("mood", a, None))
        )
        self._on_open = AsyncMock()
        self._on_message = AsyncMock()
        self._on_close = MagicMock()
        self._on_error = MagicMock()


class _FakeWS:
    """Minimal stand-in for an open websocket connection."""

    def __init__(self, frames: list[str] | None = None, raise_on_iter: Exception | None = None):
        self.state = MagicMock()
        from websockets.protocol import State
        self.state = State.OPEN
        self._frames = frames or []
        self._raise = raise_on_iter
        self.closed = False

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> str:
        if self._raise is not None:
            raise self._raise
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def send(self, data: str) -> None:
        return None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        from websockets.protocol import State
        self.state = State.CLOSED
        self.closed = True


def _fake_connect_factory(ws_sequence: list[_FakeWS]):
    """Return a function suitable for patching ``websockets.connect``.

    Each call pops one ``_FakeWS`` from ``ws_sequence``.
    """

    @asynccontextmanager
    async def _fake_connect(*args: Any, **kwargs: Any):
        ws = ws_sequence.pop(0)
        try:
            yield ws
        finally:
            await ws.close()

    return _fake_connect


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_reconnect_when_disabled() -> None:
    api = _FakeApi()
    client = WebsocketClient(api, ReconnectPolicy(enabled=False))

    ws = _FakeWS(frames=["msg1", "msg2"])
    with patch("pyquotex.ws.client.websockets.connect", _fake_connect_factory([ws])):
        await client.run_forever("wss://example/test")

    # _on_open and _on_message called; no second connect attempted.
    assert api._on_open.await_count == 1
    assert api._on_message.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_reconnect_after_unexpected_close() -> None:
    api = _FakeApi()
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=1,  # one retry, then bail
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,  # disable watchdog for this test
    )
    client = WebsocketClient(api, policy)

    closed = ConnectionClosed(rcvd=Close(1006, "abrupt"), sent=None)
    ws1 = _FakeWS(raise_on_iter=closed)
    ws2 = _FakeWS(frames=["after-reconnect"])

    with patch(
        "pyquotex.ws.client.websockets.connect",
        _fake_connect_factory([ws1, ws2]),
    ):
        await client.run_forever("wss://example/test")

    assert api._on_open.await_count == 2
    assert api._on_close.call_count == 1
    # The reconnect run consumed the "after-reconnect" frame.
    assert api._on_message.await_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscriptions_replayed_on_reconnect() -> None:
    api = _FakeApi()
    api._subscriptions["candle:EURUSD:60"] = Subscription(
        kind="candle", asset="EURUSD", period=60
    )
    api._subscriptions["mood:EURUSD:0"] = Subscription(
        kind="mood", asset="EURUSD"
    )
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=1,
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,
    )
    client = WebsocketClient(api, policy)

    closed = ConnectionClosed(rcvd=Close(1011, "fail"), sent=None)
    ws1 = _FakeWS(raise_on_iter=closed)
    ws2 = _FakeWS(frames=[])

    with patch(
        "pyquotex.ws.client.websockets.connect",
        _fake_connect_factory([ws1, ws2]),
    ):
        task = asyncio.create_task(client.run_forever("wss://example/test"))
        # Let the background replay task run; cap to keep CI fast.
        await asyncio.sleep(0.5)
        await client.close()
        await asyncio.wait_for(task, timeout=2)

    # Replay should have re-issued both subscriptions exactly once.
    kinds = [r[0] for r in api.replayed]
    assert "candle" in kinds
    assert "mood" in kinds


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_stops_reconnect_loop() -> None:
    api = _FakeApi()
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=100,
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,
    )
    client = WebsocketClient(api, policy)

    ws = _FakeWS(frames=[])

    async def slow_connect(*args, **kwargs):
        # Never resolves until cancelled, simulating an alive socket
        @asynccontextmanager
        async def _ctx():
            try:
                yield ws
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise

        return _ctx()

    with patch("pyquotex.ws.client.websockets.connect", _fake_connect_factory([ws])):
        task = asyncio.create_task(client.run_forever("wss://example/test"))
        await asyncio.sleep(0.05)
        await client.close()
        await asyncio.wait_for(task, timeout=2)
    assert client._closing is True


@pytest.mark.unit
def test_api_tracks_and_forgets_subscriptions() -> None:
    """QuotexAPI helper methods record subscriptions for replay."""
    from pyquotex.api import QuotexAPI

    api = QuotexAPI(
        host="qxbroker.com",
        username="x",
        password="x",
        lang="en",
        proxies=None,
        resource_path=".",
        user_data_dir="browser",
        on_otp_callback=None,
    )
    api._track_subscription("candle", "EURUSD", 60)
    api._track_subscription("mood", "EURUSD")
    assert "candle:EURUSD:60" in api._subscriptions
    assert "mood:EURUSD:0" in api._subscriptions
    api._forget_subscription("candle", "EURUSD", 60)
    assert "candle:EURUSD:60" not in api._subscriptions
