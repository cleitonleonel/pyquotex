"""Regression for the v1.3.0 auth-race bug.

Live trace from the field (``2026-05-06 03:07:42``):

* ``03:07:42.486`` — pyquotex sends ``42["authorization", …]``
* ``03:07:42.487`` — pyquotex.stable_api logs
  ``"Websocket failed to connect or connection was rejected."``
  *one millisecond later*
* ``03:07:43.320`` — broker replies ``42["s_authorization"]``
  (834 ms after our send)

The previous ``send_ssid()`` was fire-and-forget; the caller's
``check_connect()`` raced the broker reply and falsely concluded
auth had failed. v1.3.0 had hidden the bug behind an unconditional
2-second ``asyncio.sleep`` inside ``_check_connect`` — removing
that sleep for latency exposed the race.

The fix: ``send_ssid()`` now waits on the ``auth_changed`` event,
returning ``True`` only when ``state.auth_status == AUTHENTICATED``.
"""
from __future__ import annotations

import asyncio

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.global_value import AuthStatus


def _bare_api() -> QuotexAPI:
    """A QuotexAPI instance with no live socket; we drive
    auth_status / auth_changed directly to simulate the broker."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.state.SSID = "fake-ssid-for-test"

    # Stub the WS-level send so ``self.ssid(self.state.SSID)`` inside
    # send_ssid doesn't try to talk to a real socket.
    async def fake_send(*_a, **_kw):
        return None

    api.send_websocket_request = fake_send  # type: ignore[assignment]
    return api


@pytest.mark.asyncio
async def test_send_ssid_waits_for_broker_auth_response():
    """Reproduces the live trace: simulate s_authorization arriving
    ~50 ms after the send. The previous fire-and-forget impl returned
    True immediately and let downstream check_connect race the
    broker; the fixed impl waits for auth_changed."""
    api = _bare_api()

    async def deliver_authed_after_50ms() -> None:
        await asyncio.sleep(0.05)
        api.state.auth_status = AuthStatus.AUTHENTICATED
        await api.event_registry.set_event(
            "auth_changed", AuthStatus.AUTHENTICATED
        )

    asyncio.create_task(deliver_authed_after_50ms())
    ok = await api.send_ssid(auth_timeout=2.0)
    assert ok is True
    assert api.state.auth_status == AuthStatus.AUTHENTICATED


@pytest.mark.asyncio
async def test_send_ssid_returns_false_on_auth_timeout():
    """Broker never replies — send_ssid must time out cleanly with
    False, not hang forever."""
    api = _bare_api()
    ok = await api.send_ssid(auth_timeout=0.1)
    assert ok is False


@pytest.mark.asyncio
async def test_send_ssid_returns_false_on_authorization_reject():
    """Broker rejects the SSID — auth_changed fires but with
    AuthStatus.FAILED. send_ssid must surface that as False."""
    api = _bare_api()

    async def deliver_rejected_after_50ms() -> None:
        await asyncio.sleep(0.05)
        api.state.auth_status = AuthStatus.FAILED
        api.state.websocket_error_reason = "Websocket connection rejected."
        await api.event_registry.set_event(
            "auth_changed", AuthStatus.FAILED
        )

    asyncio.create_task(deliver_rejected_after_50ms())
    ok = await api.send_ssid(auth_timeout=2.0)
    assert ok is False
    assert api.state.auth_status == AuthStatus.FAILED


@pytest.mark.asyncio
async def test_send_ssid_returns_false_without_ssid():
    api = _bare_api()
    api.state.SSID = None
    ok = await api.send_ssid()
    assert ok is False


@pytest.mark.asyncio
async def test_send_ssid_clears_stale_auth_changed_event():
    """A previous successful connect set auth_changed; without
    clearing it, the next send_ssid would wake immediately on stale
    data even if the new broker reply hasn't landed yet."""
    api = _bare_api()

    # Pretend a previous session already fired auth_changed.
    await api.event_registry.set_event(
        "auth_changed", AuthStatus.AUTHENTICATED
    )

    # Now reset the live state to NOT_AUTHENTICATED to simulate a
    # fresh attempt after the prior session dropped.
    api.state.auth_status = AuthStatus.NOT_AUTHENTICATED

    # Don't deliver any new auth event — send_ssid should TIME OUT
    # because we cleared the stale event. If it returned True
    # immediately the test would (wrongly) succeed in <100 ms.
    started = asyncio.get_running_loop().time()
    ok = await api.send_ssid(auth_timeout=0.2)
    elapsed = asyncio.get_running_loop().time() - started

    assert ok is False
    assert elapsed >= 0.15, (
        f"send_ssid returned in {elapsed:.3f}s — stale auth_changed "
        f"event was not cleared before waiting"
    )
