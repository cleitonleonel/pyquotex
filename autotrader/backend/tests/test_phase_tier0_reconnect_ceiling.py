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
