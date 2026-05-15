"""Phase 3a broker lock scope tests (audit 2026-05-13, H2 + M3).

The fix split ``_do_connect`` so the slow ``await client.connect()``
runs OUTSIDE the manager's ``_lock``. These tests prove the split is
visible at the lock-acquisition layer — i.e. another lock-acquiring
method can grab the lock while a connect is in flight.

Heavy imports stay inside each test — see the docstring in
``test_phase0_instrumentation.py`` for the reason.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class _SlowConnectQuotex:
    """Test double whose ``connect()`` parks on a future the test
    controls. Stand-in for pyquotex during the H2 / M3 verification.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Tests share the ``release`` future via class state because
        # ``QuotexManager`` instantiates the ``Quotex`` class itself
        # via ``monkeypatch.setattr``.
        self.api = None
        self.session_data: dict[str, Any] = {}

    def set_account_mode(self, mode: Any) -> None:
        return None

    async def connect(self) -> tuple[bool, str]:
        # Park here until the test releases us. The lock should be
        # FREE during this await — that's the H2 fix being tested.
        fut = _SlowConnectQuotex.release_signal  # type: ignore[attr-defined]
        await fut
        return False, "test-controlled rejection"

    async def close(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_lock_is_free_during_slow_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While ``_do_connect`` is awaiting ``client.connect()``, the
    manager's ``_lock`` must be acquirable by an unrelated coroutine.
    Pre-Phase-3a this would deadlock until connect completed (2–30s
    in production).
    """
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _SlowConnectQuotex,
    )

    mgr = QuotexManager()
    mgr.set_credentials("test@example.com", "password", account_mode="PRACTICE")

    # Class-level future so the fake's ``connect`` and the test agree
    # on the same parking spot. Reset per test.
    loop = asyncio.get_running_loop()
    _SlowConnectQuotex.release_signal = loop.create_future()  # type: ignore[attr-defined]

    # Kick the connect — runs in the background.
    mgr.begin_connect()
    await asyncio.sleep(0.05)  # let it reach the lock-free await

    # While connect is parked, we acquire the lock ourselves. With the
    # Phase 3a split this completes immediately; pre-split it would
    # block until ``connect`` returned.
    acquired = asyncio.Event()

    async def _grab_lock() -> None:
        async with mgr._timed_lock("test-probe"):
            acquired.set()

    try:
        # Generous deadline that still catches the deadlock — pre-Phase-3a
        # the wait would be 2-30s; post-split it's < 100ms.
        await asyncio.wait_for(_grab_lock(), timeout=1.0)
    finally:
        # Always release so the connect task can finish + the test
        # can tear down cleanly.
        if not _SlowConnectQuotex.release_signal.done():  # type: ignore[attr-defined]
            _SlowConnectQuotex.release_signal.set_result(None)  # type: ignore[attr-defined]
        await mgr.wait_settled(timeout=1.0)

    assert acquired.is_set(), (
        "lock must be free during slow connect — H2 split failed"
    )


@pytest.mark.asyncio
async def test_setup_failure_still_sets_error_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Phase A (setup) raises before the lock-free await, the state
    machine still transitions to ``error`` — the new split must not
    leave the manager stuck in ``connecting``.
    """
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415

    class _BoomQuotex:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ctor blew up")

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", _BoomQuotex,
    )

    mgr = QuotexManager()
    mgr.set_credentials("test@example.com", "password", account_mode="PRACTICE")
    mgr.begin_connect()
    await mgr.wait_settled(timeout=1.0)

    snap = mgr.status()
    assert snap.state == "error"
    assert "RuntimeError" in (snap.last_error or "")
