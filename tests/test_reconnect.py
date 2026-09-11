"""Tests for the resilience layer: ReconnectPolicy + WebsocketClient.

These tests stub out the actual ``ws_connect`` call and exercise
:meth:`WebsocketClient.run_forever` to verify the auto-reconnect loop,
backoff, watchdog, and subscription replay logic in isolation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyquotex.qxtypes import ReconnectPolicy, Subscription
from pyquotex.ws.client import WebsocketClient


class _FakeSession:
    def __init__(self, ws_sequence):
        self.ws_sequence = ws_sequence
        self._closed = False

    async def ws_connect(self, *args, **kwargs):
        ws = self.ws_sequence.pop(0)
        if isinstance(ws, Exception):
            raise ws
        return ws


class _FakeApi:
    """Minimal duck-typed stand-in for QuotexAPI used by these tests."""

    def __init__(self, session=None) -> None:
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

        self.browser = MagicMock()
        self.browser.ensure_client = AsyncMock(return_value=session)
        self.browser._client = session


class _FakeWS:
    """Minimal stand-in for an open websocket connection."""

    def __init__(self, frames: list[str] | None = None, raise_on_recv: Exception | None = None):
        self._frames = frames or []
        self._raise = raise_on_recv
        self.closed = False

    async def recv(self) -> tuple[Any, Any]:
        if self._raise is not None:
            raise self._raise
        if not self._frames:
            self.closed = True
            return b"", None
        # curl_cffi recv returns (message, flags)
        return self._frames.pop(0), 1

    async def send(self, data: Any, flags: Any = None) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_reconnect_when_disabled() -> None:
    ws = _FakeWS(frames=["msg1", "msg2"])
    session = _FakeSession([ws])
    api = _FakeApi(session=session)
    client = WebsocketClient(api, ReconnectPolicy(enabled=False))

    await client.run_forever("wss://example/test")

    # _on_open and _on_message called; no second connect attempted.
    assert api._on_open.await_count == 1
    assert api._on_message.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_auto_reconnect_after_unexpected_close() -> None:
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=1,  # one retry, then bail
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,  # disable watchdog for this test
    )

    ws1 = _FakeWS(raise_on_recv=Exception("abrupt"))
    ws2 = _FakeWS(frames=["after-reconnect"])
    session = _FakeSession([ws1, ws2])
    api = _FakeApi(session=session)
    client = WebsocketClient(api, policy)

    await client.run_forever("wss://example/test")

    assert api._on_open.await_count == 2
    assert api._on_close.call_count == 1
    # The reconnect run consumed the "after-reconnect" frame.
    assert api._on_message.await_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_subscriptions_replayed_on_reconnect() -> None:
    ws1 = _FakeWS(raise_on_recv=Exception("fail"))
    ws2 = _FakeWS(frames=[])
    session = _FakeSession([ws1, ws2])

    api = _FakeApi(session=session)
    api._subscriptions["candle:EURUSD:60"] = Subscription(kind="candle", asset="EURUSD", period=60)
    api._subscriptions["mood:EURUSD:0"] = Subscription(kind="mood", asset="EURUSD")
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=1,
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,
    )
    client = WebsocketClient(api, policy)

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
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=100,
        base_delay=0.001,
        max_delay=0.005,
        jitter=0.0,
        stale_timeout=0,
    )

    class _HangingWS(_FakeWS):
        async def recv(self):
            try:
                for _ in range(50):
                    if self.closed:
                        break
                    await asyncio.sleep(0.1)
                return b"", None
            except asyncio.CancelledError:
                return b"", None

    ws = _HangingWS(frames=[])
    session = _FakeSession([ws])
    api = _FakeApi(session=session)
    client = WebsocketClient(api, policy)

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
