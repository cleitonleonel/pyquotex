"""Graceful drain tests (audit 2026-05-14, Task 6).

Spec §3.5: on lifespan shutdown, refuse new dispatches and wait up
to 300s for in-flight trades to settle before tearing down.

NOTE: The real executor class is ``TradeExecutor`` (not ``Executor``).
Tests adapted from plan pseudocode accordingly; intent is identical.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs


def test_pipeline_draining_latch_is_one_way() -> None:
    """``start_draining()`` sets ``_draining = True`` — no resume path."""
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415

    class _StubMgr:
        assets: tuple[str, ...] = ()

    class _StubExec:
        async def submit(self, **_kw: object) -> None: ...

    pipe = Pipeline(manager=_StubMgr(), executor=_StubExec())  # type: ignore[arg-type]
    assert pipe._draining is False
    pipe.start_draining()
    assert pipe._draining is True


@pytest.mark.asyncio
async def test_dispatch_refuses_when_draining() -> None:
    """A dispatch call after ``start_draining`` logs ``pipeline.refused``
    with reason='draining' and returns without calling the executor."""
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415

    class _StubMgr:
        assets: tuple[str, ...] = ("EURUSD",)

    submit_calls: list[object] = []

    class _SpyExec:
        async def submit(self, **kwargs: object) -> None:
            submit_calls.append(kwargs)

    pipe = Pipeline(manager=_StubMgr(), executor=_SpyExec())  # type: ignore[arg-type]
    pipe.start_draining()

    with capture_logs() as logs:
        await pipe.dispatch(
            RawMessage(text="CALL EURUSD 1m", chat_id=-1, sender_id=42),
        )

    refused = [r for r in logs if r["event"] == "pipeline.refused"]
    assert refused, logs
    assert refused[0]["reason"] == "draining"
    assert submit_calls == []


@pytest.mark.asyncio
async def test_wait_for_pendings_returns_when_drained() -> None:
    """When _result_watchers is empty (or all tasks are already done),
    wait_for_pendings returns 0 and logs lifespan.drain.complete.

    Uses ``TradeExecutor`` (the real class name; plan pseudocode wrote
    ``Executor`` which doesn't exist in this codebase).
    """
    import asyncio  # noqa: PLC0415

    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    # Case 1: completely empty set.
    instance = TradeExecutor.__new__(TradeExecutor)
    instance._result_watchers = set()
    with capture_logs() as logs:
        remaining = await instance.wait_for_pendings(timeout=5.0)
    assert remaining == 0
    assert any(r["event"] == "lifespan.drain.complete" for r in logs), logs

    # Case 2: set contains an already-completed task — the not t.done()
    # filter must treat it as if the set were empty.
    completed_task = asyncio.create_task(asyncio.sleep(0))
    await completed_task  # let it finish
    instance2 = TradeExecutor.__new__(TradeExecutor)
    instance2._result_watchers = {completed_task}
    with capture_logs() as logs2:
        remaining2 = await instance2.wait_for_pendings(timeout=5.0)
    assert remaining2 == 0
    assert any(r["event"] == "lifespan.drain.complete" for r in logs2), logs2


@pytest.mark.asyncio
async def test_wait_for_pendings_times_out_with_remaining_log() -> None:
    """When a result-watcher never finishes, wait_for_pendings returns the
    remaining count and logs lifespan.drain.timeout within the timeout budget.

    Uses ``TradeExecutor`` (the real class name; plan pseudocode wrote
    ``Executor`` which doesn't exist in this codebase).
    """
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415

    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    never_finishing = asyncio.create_task(asyncio.sleep(3600))
    try:
        instance = TradeExecutor.__new__(TradeExecutor)
        instance._result_watchers = {never_finishing}

        start = time.monotonic()
        with capture_logs() as logs:
            remaining = await instance.wait_for_pendings(timeout=0.2)
        elapsed = time.monotonic() - start

        assert remaining == 1
        timeouts = [r for r in logs if r["event"] == "lifespan.drain.timeout"]
        assert timeouts, logs
        assert timeouts[0]["remaining"] == 1
        # Timeout must be honored — wall time well under 1s.
        assert elapsed < 1.0, f"timeout not honored; elapsed={elapsed:.2f}s"
    finally:
        never_finishing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await never_finishing


# ---------------------------------------------------------------------------
# Critical fix: executor-side drain latch (audit 2026-05-15)
# ---------------------------------------------------------------------------


def test_executor_draining_latch_is_one_way() -> None:
    """``TradeExecutor.start_draining()`` sets ``_draining = True`` with no
    reset path, mirroring the Pipeline latch (spec §3.5 / Task 6 CRITICAL).
    """
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    instance = TradeExecutor.__new__(TradeExecutor)
    instance._draining = False
    assert instance._draining is False
    instance.start_draining()
    assert instance._draining is True
    # There must be no method that can flip it back.
    public_methods = [
        m for m in dir(instance)
        if not m.startswith("_") and callable(getattr(instance, m))
    ]
    reset_methods = [
        m for m in public_methods
        if "reset" in m.lower() or "resume" in m.lower() or "undrain" in m.lower()
    ]
    assert reset_methods == [], (
        f"start_draining() must be one-way; found potential reset methods: {reset_methods}"
    )


@pytest.mark.asyncio
async def test_auto_recovery_skipped_when_draining() -> None:
    """When ``_draining=True``, ``_fire_auto_recovery`` must return immediately
    without calling ``submit``, ``_place``, or ``_spawn_watcher``, and must log
    ``executor.auto_recovery.skipped`` with reason='draining'.

    This is the regression guard for the Task 6 CRITICAL bug: a loss settling
    mid-drain would previously spawn a new result-watcher AFTER
    wait_for_pendings had snapshotted _result_watchers, invalidating the
    drain guarantee.
    """
    import types  # noqa: PLC0415

    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    instance = TradeExecutor.__new__(TradeExecutor)
    instance._draining = True
    instance._watchers = set()
    instance._result_watchers = set()

    # SimpleNamespace stand-ins — the draining guard only reads .id on
    # both objects (for the log statement) and returns before touching
    # anything else. No SQLModel/SA initialisation needed.
    original = types.SimpleNamespace(
        id=42,
        asset="EURUSD_otc",
        direction="call",
        duration_seconds=60,
        asset_raw="EURUSD OTC",
    )
    cfg = types.SimpleNamespace(id=7)

    with patch.object(instance, "submit", new=AsyncMock()) as mock_submit, capture_logs() as logs:
        await instance._fire_auto_recovery(
            original=original,  # type: ignore[arg-type]
            cfg=cfg,            # type: ignore[arg-type]
            streak=1,
        )

    # Guard: submit must NOT have been called.
    mock_submit.assert_not_called()
    # No new watchers spawned.
    assert len(instance._result_watchers) == 0
    assert len(instance._watchers) == 0
    # The skip log entry must be present with reason='draining'.
    skipped = [
        r for r in logs
        if r["event"] == "executor.auto_recovery.skipped"
        and r.get("reason") == "draining"
    ]
    assert skipped, (
        "expected executor.auto_recovery.skipped reason=draining in logs; "
        f"got: {logs}"
    )


@pytest.mark.asyncio
async def test_spawn_watcher_tracks_in_both_sets() -> None:
    """``_spawn_watcher`` must add the spawned task to BOTH ``_watchers`` and
    ``_result_watchers`` immediately, and the done-callbacks must remove it
    from BOTH sets once it completes.

    This subset invariant is load-bearing for drain correctness:
    ``wait_for_pendings`` snapshots ``_result_watchers`` to know what to
    wait on; ``shutdown()`` cancels ``_watchers`` to clean up everything.
    A task missing from either set would corrupt the drain.
    """
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    instance = TradeExecutor.__new__(TradeExecutor)
    instance._watchers = set()
    instance._result_watchers = set()

    # Patch _watch_result to a fast no-op so the watcher completes
    # immediately and the done-callback removal fires within the test.
    async def _instant_watch(
        attempt_id: int,
        order_id: str,
        duration: int,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> None:
        return

    with patch.object(instance, "_watch_result", side_effect=_instant_watch):
        instance._spawn_watcher(
            attempt_id=99,
            order_id="test-order-1",
            duration=60,
            timeout=None,
        )

        # Immediately after spawn: task is in BOTH sets.
        assert len(instance._watchers) == 1, (
            "_watchers must contain the spawned task"
        )
        assert len(instance._result_watchers) == 1, (
            "_result_watchers must contain the spawned task"
        )
        task = next(iter(instance._watchers))
        assert task in instance._result_watchers, (
            "the task in _watchers must also be in _result_watchers"
        )

        # Let the task run to completion and the done-callbacks fire.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # After completion: done-callbacks remove from BOTH sets.
    assert len(instance._watchers) == 0, (
        "_watchers must be empty after task completes"
    )
    assert len(instance._result_watchers) == 0, (
        "_result_watchers must be empty after task completes"
    )
