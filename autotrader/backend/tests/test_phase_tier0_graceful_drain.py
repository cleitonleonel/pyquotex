"""Graceful drain tests (audit 2026-05-14, Task 6).

Spec §3.5: on lifespan shutdown, refuse new dispatches and wait up
to 300s for in-flight trades to settle before tearing down.

NOTE: The real executor class is ``TradeExecutor`` (not ``Executor``).
Tests adapted from plan pseudocode accordingly; intent is identical.
"""

from __future__ import annotations

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
async def test_wait_for_pendings_returns_when_drained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll loop returns 0 the moment list_pending() is empty.

    Uses ``TradeExecutor`` (the real class name; plan pseudocode wrote
    ``Executor`` which doesn't exist in this codebase).
    """
    import autotrader.services.executor as executor_module  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    calls = {"count": 0}

    async def _list_pending_stub(_session: object) -> list[object]:
        calls["count"] += 1
        return []

    monkeypatch.setattr(executor_module, "list_pending", _list_pending_stub)

    instance = TradeExecutor.__new__(TradeExecutor)
    remaining = await instance.wait_for_pendings(timeout=5.0)

    assert remaining == 0
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_wait_for_pendings_times_out_with_remaining_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pendings never drain, the helper returns the remaining
    count and logs ``lifespan.drain.timeout``.

    Uses ``TradeExecutor`` (the real class name; plan pseudocode wrote
    ``Executor`` which doesn't exist in this codebase).
    """
    import autotrader.services.executor as executor_module  # noqa: PLC0415
    from autotrader.services.executor import TradeExecutor  # noqa: PLC0415

    class _FakeRow:
        id = 99

    async def _list_pending_stub(_session: object) -> list[object]:
        return [_FakeRow(), _FakeRow()]

    monkeypatch.setattr(executor_module, "list_pending", _list_pending_stub)

    instance = TradeExecutor.__new__(TradeExecutor)
    with capture_logs() as logs:
        remaining = await instance.wait_for_pendings(timeout=0.5)

    assert remaining == 2
    timeouts = [r for r in logs if r["event"] == "lifespan.drain.timeout"]
    assert timeouts, logs
    assert timeouts[0]["remaining"] == 2
