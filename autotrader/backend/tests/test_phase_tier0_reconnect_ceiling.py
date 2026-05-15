"""Hard-ceiling reconnect tests (audit 2026-05-14, Task 4).

Spec §3.3 splits the existing cosmetic _HARD_OUTAGE_AFTER_ATTEMPTS
into two constants:

* ``_SOFT_DOWNGRADE_AFTER_ATTEMPTS = 10`` — keep the UX downgrade.
* ``broker_reconnect_hard_ceiling`` (env-overridable, default 20)
  — stop the pyquotex supervisor, disconnect, flip state.

A successful reconnect anywhere below the ceiling resets the
internal counter.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_soft_downgrade_at_10_keeps_supervisor_running() -> None:
    """At attempt 10, the event flips recoverable=False but the
    supervisor IS NOT stopped — the operator sees an outage warning
    while pyquotex keeps trying."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()

    mgr._client = MagicMock()  # type: ignore[assignment]
    mgr._client.api.reconnect_supervisor.stop = AsyncMock()  # type: ignore[union-attr]
    mgr._client.disconnect = AsyncMock()  # type: ignore[union-attr]

    mgr._on_reconnect_attempt_failed(10)

    mgr._client.api.reconnect_supervisor.stop.assert_not_called()  # type: ignore[union-attr]
    mgr._client.disconnect.assert_not_called()  # type: ignore[union-attr]
    assert mgr._state != "awaiting_manual_recovery"


@pytest.mark.asyncio
async def test_hard_ceiling_disconnects_and_flips_state() -> None:
    """At attempt 20 (default), supervisor is stopped, client
    disconnects, and state flips to ``awaiting_manual_recovery``."""
    import asyncio  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr._state = "reconnecting"  # type: ignore[assignment]
    mgr._client = MagicMock()  # type: ignore[assignment]
    mgr._client.api.reconnect_supervisor.stop = AsyncMock()  # type: ignore[union-attr]
    mgr._client.disconnect = AsyncMock()  # type: ignore[union-attr]

    mgr._on_reconnect_attempt_failed(20)
    assert mgr._ceiling_halt_task is not None
    await asyncio.wait_for(mgr._ceiling_halt_task, timeout=1.0)

    mgr._client.api.reconnect_supervisor.stop.assert_awaited_once()  # type: ignore[union-attr]
    mgr._client.disconnect.assert_awaited_once()  # type: ignore[union-attr]
    assert mgr._state == "awaiting_manual_recovery"
    assert "ceiling reached" in (mgr._last_error or "").lower()


def test_successful_reconnect_resets_counter() -> None:
    """_on_ws_recovered already clears _consecutive_failed_reconnects."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()
    mgr._consecutive_failed_reconnects = 15  # type: ignore[assignment]
    mgr._disconnected_at = None  # type: ignore[assignment]

    mgr._on_ws_recovered()

    assert mgr._consecutive_failed_reconnects == 0


def test_on_ws_dropped_does_not_unstick_manual_recovery() -> None:
    """I3-B: once the manager is in the terminal awaiting_manual_recovery
    state, a status-watcher WS-drop edge must NOT flip it back to
    reconnecting — the operator must run /reconnect to leave it.

    _on_ws_dropped's guard (line 857) is the FIRST statement in the
    method, before any access to attributes on the ``state`` argument,
    so a bare MagicMock satisfies the call without crashing.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr._state = "awaiting_manual_recovery"  # type: ignore[assignment]
    mgr._disconnected_at = None  # type: ignore[assignment]

    # The guard `if self._state == "awaiting_manual_recovery": return`
    # is the very first line of _on_ws_dropped, so it returns before
    # reading any attribute off `state`.  A plain MagicMock is the
    # minimal faithful stub: it exercises the real guard without
    # crashing on attribute access that never reaches this path.
    mgr._on_ws_dropped(MagicMock())

    assert mgr._state == "awaiting_manual_recovery"
    assert mgr._disconnected_at is None  # early-return skipped the mutation


@pytest.mark.asyncio
async def test_disconnect_cancels_in_flight_ceiling_halt() -> None:
    """I1: if _halt_at_ceiling is mid-flight when disconnect() is
    called, _stop_ceiling_halt_task cancels it so it can't race into
    the finalize lock and resurrect awaiting_manual_recovery after
    disconnect() set state=idle.

    Wiring choice: we call ``mgr._stop_ceiling_halt_task()`` directly
    rather than the full ``disconnect()`` path.  Rationale: ``disconnect()``
    calls ``cancel_connect()`` → ``_stop_status_watcher()`` →
    ``_stop_ceiling_halt_task()`` → takes the lock → calls
    ``self._client.close()``.  The assertion we care about (I1) is
    satisfied entirely by ``_stop_ceiling_halt_task`` — it cancels and
    awaits the task then sets ``_ceiling_halt_task = None``.  Driving
    the full ``disconnect()`` would require mocking ``_client.close``
    (AsyncMock) AND ensuring ``cancel_connect`` / ``_stop_status_watcher``
    don't interfere, adding setup noise unrelated to I1.  Calling
    ``_stop_ceiling_halt_task`` directly is both the narrower and more
    deterministic test of the invariant.
    """
    import asyncio  # noqa: PLC0415

    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    mgr = QuotexManager()
    mgr._state = "reconnecting"  # type: ignore[assignment]
    mgr._client = MagicMock()  # type: ignore[assignment]

    # Make supervisor.stop() hang so the halt task is reliably
    # in-flight (suspended) when we cancel it.
    started = asyncio.Event()

    async def _hang() -> None:
        started.set()
        await asyncio.sleep(3600)

    mgr._client.api.reconnect_supervisor.stop = AsyncMock(side_effect=_hang)
    mgr._client.disconnect = AsyncMock()

    # Spawn the halt task directly (bypassing the failed-count path so
    # the test is deterministic) and wait until it's parked in stop().
    mgr._ceiling_halt_task = asyncio.create_task(
        mgr._halt_at_ceiling(20, 20),
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # Cancel the in-flight task via the same helper disconnect() uses.
    await mgr._stop_ceiling_halt_task()

    assert mgr._ceiling_halt_task is None
    # The halt task was cancelled before it could reach the finalize
    # lock, so it did NOT write awaiting_manual_recovery.
    assert mgr._state != "awaiting_manual_recovery"
