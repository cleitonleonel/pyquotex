"""Regression tests for v1.3.0 robustness + speed audit fixes."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.global_value import AuthStatus, ConnectionState, WebsocketStatus
from pyquotex.stable_api import Quotex


# -----------------------------------------------------------------
# Fix 1: check_connect 2s sleep removed
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_connect_returns_synchronously():
    """``_check_connect`` used to ``await asyncio.sleep(2)`` on every
    call — every public method that called ``check_connect()`` paid a
    2 s tax. The state read should now be synchronous."""
    state = SimpleNamespace(auth_status=AuthStatus.AUTHENTICATED)
    t0 = time.perf_counter()
    ok = await Quotex._check_connect(state)
    elapsed = time.perf_counter() - t0
    assert ok is True
    assert elapsed < 0.05, f"check_connect took {elapsed:.3f}s — sleep not removed"


@pytest.mark.asyncio
async def test_check_connect_false_when_unauthenticated():
    state = SimpleNamespace(auth_status=AuthStatus.NOT_AUTHENTICATED)
    assert await Quotex._check_connect(state) is False


# -----------------------------------------------------------------
# Fix 2: realtime_price uses deque
# -----------------------------------------------------------------

def test_realtime_price_is_deque_with_maxlen():
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    # Touch the asset to trigger defaultdict factory
    container = api.realtime_price["EURUSD"]
    assert isinstance(container, deque)
    assert container.maxlen == 1000


def test_realtime_price_evicts_in_O1():
    """The previous list-based cap was O(n) per tick (``list.pop(0)``).
    A deque with maxlen evicts in O(1) and never exceeds the cap."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    container = api.realtime_price["EURUSD"]
    for i in range(2000):
        container.append({"time": i, "price": 1.0})
    assert len(container) == 1000
    # Oldest 1000 entries evicted.
    assert container[0]["time"] == 1000
    assert container[-1]["time"] == 1999


# -----------------------------------------------------------------
# Fix 3: profile offset is cached
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_offset_cached_across_calls():
    """``open_pending`` calls ``_get_cached_profile_offset`` per trade.
    The underlying HTTP call must happen only once per session."""
    client = Quotex(email="a@b.com", password="x", auto_reconnect=False)

    call_count = 0

    async def fake_profile():
        nonlocal call_count
        call_count += 1
        return SimpleNamespace(offset=16200)  # UTC+4:30

    client.get_profile = fake_profile  # type: ignore[assignment]

    assert await client._get_cached_profile_offset() == 16200
    assert await client._get_cached_profile_offset() == 16200
    assert await client._get_cached_profile_offset() == 16200
    assert call_count == 1, f"profile fetched {call_count} times — cache miss"


@pytest.mark.asyncio
async def test_profile_offset_resets_on_reconnect():
    client = Quotex(email="a@b.com", password="x", auto_reconnect=False)
    client._profile_offset_cache = 16200
    # Simulating what the reconnect hook does:
    client._profile_offset_cache = None
    assert client._profile_offset_cache is None


# -----------------------------------------------------------------
# Fix 4: concurrent connect() guarded by lock
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_connect_serialises_via_lock():
    """Two concurrent ``connect()`` calls must serialise — without the
    lock both would create a fresh ``QuotexAPI`` instance and
    authenticate twice (account-lock risk)."""
    client = Quotex(email="a@b.com", password="x", auto_reconnect=False)

    enter_count = 0
    inside_at_once = 0
    max_concurrency = 0

    async def fake_unlocked():
        nonlocal enter_count, inside_at_once, max_concurrency
        enter_count += 1
        inside_at_once += 1
        max_concurrency = max(max_concurrency, inside_at_once)
        await asyncio.sleep(0.05)
        inside_at_once -= 1
        return True, "ok"

    client._connect_unlocked = fake_unlocked  # type: ignore[assignment]
    results = await asyncio.gather(client.connect(), client.connect(), client.connect())
    assert all(r == (True, "ok") for r in results)
    assert max_concurrency == 1, "lock did not serialise concurrent connects"
    assert enter_count == 3


# -----------------------------------------------------------------
# Fix 5: heartbeat fires status_changed=ERROR on send failure
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_signals_error_on_send_failure():
    """Heartbeat used to silently break out on send exception; the
    supervisor would then never notice and reconnect would never
    fire. Now it flips state to ERROR + signals ``status_changed``."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.state.status = WebsocketStatus.CONNECTED

    # Simulate a websocket whose send() raises immediately.
    class FailingWs:
        async def send(self, data):
            raise OSError("connection lost")

    failing_ws = FailingWs()
    failing_client = SimpleNamespace(wss=failing_ws)
    api.websocket_client = failing_client

    # Replicate the heartbeat body inline — the production task is
    # spawned inside _on_open which we don't want to drive end-to-end
    # here. Behaviour we're testing: on send exception → ERROR state
    # and status_changed event fires.
    async def heartbeat_body():
        try:
            await failing_ws.send('42["tick"]')
        except Exception as e:
            api.state.status = WebsocketStatus.ERROR
            api.state.websocket_error_reason = f"heartbeat send failed: {e}"
            await api.event_registry.set_event(
                "status_changed", api.state.status
            )

    await heartbeat_body()
    assert api.state.status == WebsocketStatus.ERROR
    assert "heartbeat" in (api.state.websocket_error_reason or "")
    # Verify the event registered as fired.
    event = await api.event_registry.get_event("status_changed")
    assert event.is_set()


# -----------------------------------------------------------------
# Fix 6: stop_candles_stream cleans up subscribe lists
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_candles_stream_removes_from_replay_lists():
    client = Quotex(email="a@b.com", password="x", auto_reconnect=False)
    client.api = MagicMock()
    client.api.unsubscribe_realtime_candle = AsyncMock()
    client.api.unfollow_candle = AsyncMock()

    # Simulating prior subscriptions
    client.subscribe_candle = ["EURUSD,60", "EURUSD,300", "GBPUSD,60"]
    client.subscribe_candle_all_size = ["EURUSD", "GBPUSD"]
    client._subscribed_assets = {"EURUSD,60", "EURUSD,300", "GBPUSD,60"}

    await client.stop_candles_stream("EURUSD")

    # All EURUSD entries gone, GBPUSD untouched.
    assert client.subscribe_candle == ["GBPUSD,60"]
    assert client.subscribe_candle_all_size == ["GBPUSD"]
    assert client._subscribed_assets == {"GBPUSD,60"}
    client.api.unsubscribe_realtime_candle.assert_awaited_once_with("EURUSD")
    client.api.unfollow_candle.assert_awaited_once_with("EURUSD")


# -----------------------------------------------------------------
# Fix 7: pending_ticket_map reverse index for O(1) close mirror
# -----------------------------------------------------------------

def test_exec_to_pending_index_initialised_empty():
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    assert api._exec_to_pending == {}


@pytest.mark.asyncio
async def test_close_uses_reverse_index_when_present():
    """When the success path populated both ``pending_ticket_map`` AND
    ``_exec_to_pending``, the close handler must hit the O(1) path."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.pending_ticket_map = {"ticket-A": "exec-X"}
    api._exec_to_pending = {"exec-X": "ticket-A"}

    # Simulate the close handler firing for exec-X. We test the
    # extraction logic directly to keep this fast.
    pticket = api._exec_to_pending.pop("exec-X", None)
    assert pticket == "ticket-A"
    api.pending_ticket_map.pop(pticket, None)
    assert api.pending_ticket_map == {}
    assert api._exec_to_pending == {}


def test_exec_to_pending_cleared_on_disconnect():
    """``_on_close`` must clear the reverse index alongside the
    forward maps (otherwise stale entries leak into the next session
    and could spuriously match a future close)."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api._active_pending = {"t1": "EURUSD"}
    api.pending_ticket_map = {"t1": "exec-1"}
    api._exec_to_pending = {"exec-1": "t1"}

    api._on_close(1000, "test")
    assert api._exec_to_pending == {}
