"""Broker disconnect / auto-recovery resilience.

These tests cover the gap between pyquotex's ``ReconnectSupervisor``
(which silently reconnects under the hood) and the ``QuotexManager``
upper layer (which used to be blind to those drops):

* ``manager.connected`` must reflect the *real* WebSocket state, not
  Python-object existence. Before the fix it stayed ``True`` for the
  life of the process after a successful login, even when the socket
  had been dead for minutes — silently letting the risk gate ship
  trades into a closed pipe.

* On a CONNECTED → DISCONNECTED transition, the manager must publish a
  ``system.error`` event so the admin bot can notify the operator —
  for a real-money trading bot, silent downtime is the worst class of
  bug.

* On the way back up (DISCONNECTED → CONNECTED + AUTHENTICATED), the
  manager must move out of the ``reconnecting`` state automatically,
  without the operator having to manually re-click "Connect".

The test stub flips the per-instance ``state.status`` /
``state.auth_status`` fields the same way pyquotex's ``_on_close`` /
``_on_open`` do — that's the only seam the manager needs to observe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from autotrader.services.event_bus import TradeEvent, TradeEventBus
from autotrader.services.quotex_manager import QuotexManager
from pyquotex.global_value import AuthStatus, WebsocketStatus


# ---------------------------------------------------------------------------
# A FakeQuotex that models the WS state we need to flip in-test.
# ---------------------------------------------------------------------------


@dataclass
class _FakeState:
    status: WebsocketStatus = WebsocketStatus.DISCONNECTED
    auth_status: AuthStatus = AuthStatus.NOT_AUTHENTICATED
    websocket_error_reason: str | None = None


@dataclass
class _FakeReconnectStats:
    """Mirrors :class:`pyquotex.utils.reconnect.ReconnectStats`."""

    attempts: int = 0
    successful_reconnects: int = 0
    failed_reconnects: int = 0
    last_error: str | None = None


@dataclass
class _FakeReconnectSupervisor:
    stats: _FakeReconnectStats = field(default_factory=_FakeReconnectStats)


@dataclass
class _FakeApi:
    state: _FakeState = field(default_factory=_FakeState)
    reconnect_supervisor: _FakeReconnectSupervisor = field(
        default_factory=_FakeReconnectSupervisor,
    )


class FakeQuotex:
    """Just enough of :class:`pyquotex.stable_api.Quotex` for the
    resilience tests — exposes a real ``api.state`` we can mutate."""

    def __init__(self, **_: Any) -> None:
        self.api = _FakeApi()
        self.account_mode_set: str | None = None

    def set_account_mode(self, mode: str) -> None:
        self.account_mode_set = mode

    async def connect(self) -> tuple[bool, str]:
        # Simulate the steady-state pyquotex emits after login completes.
        self.api.state.status = WebsocketStatus.CONNECTED
        self.api.state.auth_status = AuthStatus.AUTHENTICATED
        return True, "ok"

    async def close(self) -> bool:
        self.api.state.status = WebsocketStatus.DISCONNECTED
        self.api.state.auth_status = AuthStatus.NOT_AUTHENTICATED
        return True

    async def get_all_assets(self) -> dict[str, str]:
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_connected_manager(
    monkeypatch: pytest.MonkeyPatch,
    event_bus: TradeEventBus | None = None,
) -> QuotexManager:
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )
    mgr = QuotexManager(root_path=".", event_bus=event_bus)
    mgr.set_credentials("u@v.com", "pw", "PRACTICE")
    mgr.begin_connect()
    # `wait_settled` polls every 25ms until the connect leaves the
    # transient `connecting` state.
    await mgr.wait_settled(timeout=2.0)
    assert mgr.status().state == "connected", mgr.status().last_error
    return mgr


def _collect_events(bus: TradeEventBus) -> tuple[list[TradeEvent], asyncio.Task]:
    """Subscribe to ``bus`` and accumulate events on a background task.

    The returned task must be cancelled by the caller; the list is
    appended to in place as events arrive.
    """
    events: list[TradeEvent] = []

    async def _drain() -> None:
        async for ev in bus.subscribe():
            events.append(ev)

    task = asyncio.create_task(_drain())
    return events, task


@pytest.fixture
def event_loop_policy() -> Iterator[None]:
    """Each test gets its own loop — manager spawns tasks that we want
    isolated across cases."""
    yield


# ---------------------------------------------------------------------------
# 1. ``connected`` must reflect real WS state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connected_flips_false_when_ws_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old bug: ``connected`` was ``self._client.api is not None`` —
    permanently true once we've ever connected. Now it must mirror the
    underlying ``state.status``."""
    mgr = await _make_connected_manager(monkeypatch)
    try:
        assert mgr.connected is True

        # Simulate pyquotex's `_on_close` → state transitions.
        client = mgr._client
        assert client is not None
        client.api.state.status = WebsocketStatus.DISCONNECTED

        assert mgr.connected is False, (
            "manager still reports connected after WS dropped — risk "
            "gate would happily ship trades into a closed pipe"
        )
    finally:
        await mgr.disconnect()


@pytest.mark.asyncio
async def test_connected_requires_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WS that's open but un-authorised is *not* trade-ready —
    pyquotex's supervisor explicitly handles this when a stale SSID
    survives a TCP reconnect."""
    mgr = await _make_connected_manager(monkeypatch)
    try:
        client = mgr._client
        assert client is not None
        # WS is up, auth was rejected → not actually usable.
        client.api.state.status = WebsocketStatus.CONNECTED
        client.api.state.auth_status = AuthStatus.FAILED
        assert mgr.connected is False
    finally:
        await mgr.disconnect()


# ---------------------------------------------------------------------------
# 2. Disconnect must surface a system.error event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_publishes_system_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = TradeEventBus()
    events, drain_task = _collect_events(bus)

    mgr = await _make_connected_manager(monkeypatch, event_bus=bus)
    try:
        # Yield once so the drain task is subscribed before we publish.
        await asyncio.sleep(0)

        client = mgr._client
        assert client is not None
        client.api.state.status = WebsocketStatus.DISCONNECTED
        client.api.state.websocket_error_reason = "broker rotated session"

        # The watcher polls quickly — give it a generous window.
        for _ in range(40):
            await asyncio.sleep(0.05)
            disconnect_events = [
                e for e in events
                if e.type == "system.error"
                and e.payload.get("kind") == "broker.disconnected"
            ]
            if disconnect_events:
                break

        kinds = [e.payload.get("kind") for e in events if e.type == "system.error"]
        assert "broker.disconnected" in kinds, (
            f"expected broker.disconnected in system.error events, got {kinds}"
        )
        # Status surfaces this as `reconnecting` so the UI shows the
        # gap rather than a stale "Connected" pill.
        assert mgr.status().state == "reconnecting"
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await mgr.disconnect()


# ---------------------------------------------------------------------------
# 3. Recovery returns us to ``connected`` automatically
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loud_policy_fires_on_every_failed_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LOUD policy contract: every failed reconnect attempt past the
    initial drop must produce one ``broker.recover_stalled`` event.

    The operator picked this policy on 2026-05-12 to maximise visibility
    when running on real money. The first few failures are flagged
    ``recoverable=True`` (transient); past ``_SOFT_DOWNGRADE_AFTER_ATTEMPTS``
    the flag flips to ``False`` so the admin bot escalates the tone.
    """
    bus = TradeEventBus()
    events, drain_task = _collect_events(bus)

    mgr = await _make_connected_manager(monkeypatch, event_bus=bus)
    try:
        await asyncio.sleep(0)
        client = mgr._client
        assert client is not None

        # Disconnect → ``broker.disconnected`` fires once; subsequent
        # failed-retry increments must each emit a separate
        # ``broker.recover_stalled``.
        client.api.state.status = WebsocketStatus.DISCONNECTED

        # Drive the supervisor stats forward in lockstep with the
        # watcher's poll cadence. Three increments → three events.
        for i in range(1, 4):
            client.api.reconnect_supervisor.stats.failed_reconnects = i
            # 3 poll cycles ≫ one watcher tick (100ms)
            for _ in range(3):
                await asyncio.sleep(0.05)

        stalled = [
            e for e in events
            if e.type == "system.error"
            and e.payload.get("kind") == "broker.recover_stalled"
        ]
        assert len(stalled) == 3, (
            f"loud policy contract violated — expected 3 stalled events, "
            f"got {len(stalled)}: "
            f"{[e.payload.get('detail') for e in stalled]}"
        )
        # All three are still soft (recoverable=True) — we haven't
        # hit the hard-outage threshold yet.
        assert all(e.payload.get("recoverable") is True for e in stalled)
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await mgr.disconnect()


@pytest.mark.asyncio
async def test_loud_policy_flips_recoverable_after_hard_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past ``_SOFT_DOWNGRADE_AFTER_ATTEMPTS`` (10), the event flips to
    ``recoverable=False`` so the admin bot stops downplaying it."""
    from autotrader.services.quotex_manager import QuotexManager

    threshold = QuotexManager._SOFT_DOWNGRADE_AFTER_ATTEMPTS

    bus = TradeEventBus()
    events, drain_task = _collect_events(bus)

    mgr = await _make_connected_manager(monkeypatch, event_bus=bus)
    try:
        await asyncio.sleep(0)
        client = mgr._client
        assert client is not None
        client.api.state.status = WebsocketStatus.DISCONNECTED

        # Bump straight to the threshold so the test stays fast — the
        # watcher only cares about the *delta*, not the path taken.
        client.api.reconnect_supervisor.stats.failed_reconnects = threshold
        for _ in range(5):
            await asyncio.sleep(0.05)

        stalled = [
            e for e in events
            if e.type == "system.error"
            and e.payload.get("kind") == "broker.recover_stalled"
        ]
        assert stalled, "no broker.recover_stalled event surfaced"
        # The event emitted at the threshold tick must be non-recoverable.
        assert stalled[-1].payload.get("recoverable") is False, (
            f"expected recoverable=False at attempt {threshold}, got "
            f"{stalled[-1].payload}"
        )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await mgr.disconnect()


@pytest.mark.asyncio
async def test_reconnect_clears_reconnecting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = TradeEventBus()
    events, drain_task = _collect_events(bus)

    mgr = await _make_connected_manager(monkeypatch, event_bus=bus)
    try:
        await asyncio.sleep(0)
        client = mgr._client
        assert client is not None

        # Drop.
        client.api.state.status = WebsocketStatus.DISCONNECTED
        for _ in range(40):
            await asyncio.sleep(0.05)
            if mgr.status().state == "reconnecting":
                break
        assert mgr.status().state == "reconnecting"

        # Supervisor brings it back (simulated).
        client.api.state.status = WebsocketStatus.CONNECTED
        client.api.state.auth_status = AuthStatus.AUTHENTICATED

        for _ in range(40):
            await asyncio.sleep(0.05)
            if mgr.status().state == "connected":
                break
        assert mgr.status().state == "connected", (
            "manager did not flip back to connected after WS recovered"
        )
        assert mgr.connected is True
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await mgr.disconnect()
