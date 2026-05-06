"""Tests for the auto-reconnect supervisor."""
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from pyquotex.global_value import WebsocketStatus
from pyquotex.utils.async_utils import EventRegistry
from pyquotex.utils.reconnect import (
    ReconnectPolicy,
    ReconnectStats,
    ReconnectSupervisor,
)


def _fake_api(start_results: list[tuple[bool, str]]) -> SimpleNamespace:
    """Build a stubbed api object the supervisor can drive."""
    state = SimpleNamespace(
        status=WebsocketStatus.CONNECTED, websocket_error_reason=None
    )

    async def start_websocket() -> tuple[bool, str]:
        result = start_results.pop(0)
        if result[0]:
            state.status = WebsocketStatus.CONNECTED
        return result

    async def send_ssid() -> bool:
        return True

    async def change_account(account_type: int, tournament_id: int = 0) -> None:
        api.changed_account = (account_type, tournament_id)

    api = SimpleNamespace(
        state=state,
        event_registry=EventRegistry(),
        account_type=1,
        tournament_id=0,
        start_websocket=start_websocket,
        send_ssid=send_ssid,
        change_account=change_account,
        _client_ref=None,
        changed_account=None,
    )
    return api


def test_policy_defaults():
    p = ReconnectPolicy()
    assert p.enabled is True
    assert p.max_attempts == -1
    assert p.initial_delay == 1.0


def test_stats_default_zero():
    s = ReconnectStats()
    assert s.attempts == 0
    assert s.last_disconnect_at is None


async def _wait_until(predicate, timeout: float = 2.0) -> bool:
    """Spin until ``predicate()`` is truthy or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_supervisor_reconnects_after_disconnect():
    api = _fake_api(start_results=[(True, "Connected")])
    sup = ReconnectSupervisor(
        api,
        ReconnectPolicy(initial_delay=0.0, jitter=0.0, max_attempts=3),
    )
    sup.poll_interval = 0.01
    sup.capture()
    sup.start()
    await asyncio.sleep(0)

    api.state.status = WebsocketStatus.DISCONNECTED
    ok = await _wait_until(lambda: sup.stats.successful_reconnects == 1)
    await sup.stop()

    assert ok, "supervisor never recorded a reconnect"
    assert sup.stats.attempts == 1


@pytest.mark.asyncio
async def test_supervisor_gives_up_after_max_attempts():
    api = _fake_api(start_results=[(False, "fail")] * 3)
    sup = ReconnectSupervisor(
        api,
        ReconnectPolicy(initial_delay=0.0, jitter=0.0, max_attempts=3),
    )
    sup.poll_interval = 0.01
    sup.start()
    await asyncio.sleep(0)
    api.state.status = WebsocketStatus.DISCONNECTED
    ok = await _wait_until(lambda: sup.stats.failed_reconnects == 3)
    await sup.stop()

    assert ok, "supervisor never gave up after max attempts"
    assert sup.stats.attempts == 3
    assert sup.stats.successful_reconnects == 0


@pytest.mark.asyncio
async def test_supervisor_replays_account_type_after_reconnect():
    api = _fake_api(start_results=[(True, "ok")])
    sup = ReconnectSupervisor(
        api, ReconnectPolicy(initial_delay=0.0, jitter=0.0, max_attempts=1)
    )
    sup.poll_interval = 0.01
    api.account_type = 0
    api.tournament_id = 7
    sup.capture()

    sup.start()
    await asyncio.sleep(0)
    api.state.status = WebsocketStatus.DISCONNECTED
    ok = await _wait_until(lambda: api.changed_account is not None)
    await sup.stop()

    assert ok
    assert api.changed_account == (0, 7)


@pytest.mark.asyncio
async def test_disabled_policy_does_not_reconnect():
    api = _fake_api(start_results=[])
    sup = ReconnectSupervisor(
        api, ReconnectPolicy(enabled=False, initial_delay=0.0, jitter=0.0)
    )
    sup.poll_interval = 0.01
    sup.start()
    await asyncio.sleep(0)
    api.state.status = WebsocketStatus.DISCONNECTED
    await asyncio.sleep(0.1)
    await sup.stop()
    assert sup.stats.attempts == 0


@pytest.mark.asyncio
async def test_supervisor_treats_failed_send_ssid_as_failed_attempt():
    """Quotex SSIDs expire after a few hours. Once expired, the WS
    handshake still succeeds but ``s_authorization`` is rejected and
    ``send_ssid()`` returns False. Previously the supervisor ignored
    that bool, declared the reconnect successful, and exited the loop
    leaving the API stuck with ``connected=False, auth=FAILED``. The
    fix:
      * count a False ``send_ssid()`` as a failed attempt
      * clear the cached SSID so the next attempt re-runs HTTP login
    """
    state = SimpleNamespace(
        status=WebsocketStatus.CONNECTED,
        websocket_error_reason=None,
        SSID="cached-but-now-expired",
    )

    async def start_websocket() -> tuple[bool, str]:
        state.status = WebsocketStatus.CONNECTED
        return True, "Connected"

    # First call: stale SSID — broker rejects. Second call: succeeds.
    ssid_results = [False, True]

    async def send_ssid() -> bool:
        return ssid_results.pop(0)

    async def change_account(*_a, **_kw) -> None:
        pass

    api = SimpleNamespace(
        state=state,
        event_registry=EventRegistry(),
        account_type=1,
        tournament_id=0,
        start_websocket=start_websocket,
        send_ssid=send_ssid,
        change_account=change_account,
        _client_ref=None,
    )

    sup = ReconnectSupervisor(
        api,
        ReconnectPolicy(initial_delay=0.0, jitter=0.0, max_attempts=5),
    )
    sup.poll_interval = 0.01
    sup.capture()
    sup.start()
    await asyncio.sleep(0)
    api.state.status = WebsocketStatus.DISCONNECTED

    ok = await _wait_until(lambda: sup.stats.successful_reconnects == 1)
    await sup.stop()

    assert ok, "supervisor never recovered after stale-SSID rejection"
    # The first attempt was an auth failure → counted as failed,
    # not successful. The second attempt (with a fresh login) wins.
    assert sup.stats.failed_reconnects == 1
    assert sup.stats.successful_reconnects == 1
    # SSID cleared after the rejection so start_websocket() will
    # re-run authenticate() on the next attempt.
    assert api.state.SSID == ""
    assert "expired" in (sup.stats.last_error or "").lower() or \
        "rejected" in (sup.stats.last_error or "").lower()


@pytest.mark.asyncio
async def test_on_reconnect_callback_fires():
    api = _fake_api(start_results=[(True, "ok")])
    sup = ReconnectSupervisor(
        api, ReconnectPolicy(initial_delay=0.0, jitter=0.0, max_attempts=1)
    )
    sup.poll_interval = 0.01
    fired = asyncio.Event()

    async def cb() -> None:
        fired.set()

    sup.on_reconnect(cb)
    sup.start()
    await asyncio.sleep(0)
    api.state.status = WebsocketStatus.DISCONNECTED
    await asyncio.wait_for(fired.wait(), timeout=2.0)
    await sup.stop()
