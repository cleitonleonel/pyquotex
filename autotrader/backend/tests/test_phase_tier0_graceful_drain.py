"""Graceful drain tests (audit 2026-05-14, Task 6).

Spec §3.5: on lifespan shutdown, refuse new dispatches and wait up
to 300s for in-flight trades to settle before tearing down.

NOTE: The real executor class is ``TradeExecutor`` (not ``Executor``).
Tests adapted from plan pseudocode accordingly; intent is identical.
"""

from __future__ import annotations

import contextlib

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
