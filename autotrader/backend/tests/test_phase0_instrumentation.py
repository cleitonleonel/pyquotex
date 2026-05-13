"""Phase 0 instrumentation tests (audit 2026-05-13).

Each instrumentation point added in the Phase 0 commit emits a
structured log line under a well-defined condition; this module
asserts that the line fires when (and only when) it should. Pure
observability — none of these tests assert behaviour changes.

Tests use ``structlog.testing.capture_logs`` as the assertion surface;
that's the lowest-overhead way to verify structlog calls without
mocking the underlying logger.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

# Heavy autotrader imports are deferred to inside each test. Pulling
# them at module top-level reorders the import graph relative to
# ``conftest.py`` / other test modules just enough to leak DB-engine
# state between test files in the same pytest invocation — observed
# when this file collected before ``test_pipeline.py`` and triggered
# ``no such table`` errors on tests that ran fine otherwise. Inline
# imports keep the side-effects scoped to the test that needs them.


# ---------------------------------------------------------------------------
# pipeline.duplicate_candidate
# ---------------------------------------------------------------------------


class _StubManager:
    assets: tuple[str, ...] = ()


class _StubExecutor:
    async def submit(self, **kwargs: object) -> None:  # pragma: no cover
        return None


def _make_pipeline() -> object:
    """Deferred-import factory — see module docstring for the reason."""
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415
    return Pipeline(manager=_StubManager(), executor=_StubExecutor())


def _raw(text: str, chat_id: int = -1001, sender_id: int = 42) -> object:
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415
    return RawMessage(text=text, chat_id=chat_id, sender_id=sender_id)


def test_duplicate_candidate_fires_on_replay_within_window() -> None:
    """Two messages with the same ``(text[:200], sender_id)`` for the
    same chat within ``_DUPLICATE_WINDOW`` must emit a single
    ``pipeline.duplicate_candidate`` warning, and the same key arriving
    a *third* time emits another one. The detector is the canary for
    H1 (Pyrogram replay → duplicate trades) until Phase 2 plumbs a real
    ``message_id`` through."""
    pipe = _make_pipeline()
    msg = _raw("CALL EURUSD 1m")

    with capture_logs() as logs:
        pipe._check_duplicate_candidate(msg)
        pipe._check_duplicate_candidate(msg)
        pipe._check_duplicate_candidate(msg)

    dupes = [r for r in logs if r["event"] == "pipeline.duplicate_candidate"]
    assert len(dupes) == 2, (
        "first call seeds; second + third are dupes — got "
        f"{[r['event'] for r in logs]}"
    )
    assert dupes[0]["chat_id"] == -1001
    assert dupes[0]["sender_id"] == 42
    assert "text_preview" in dupes[0]
    assert "elapsed_seconds" in dupes[0]


def test_duplicate_candidate_silent_when_text_differs() -> None:
    """Different ``text`` from the same sender does NOT trip the
    detector — the channel is just chatty, not replaying."""
    pipe = _make_pipeline()

    with capture_logs() as logs:
        pipe._check_duplicate_candidate(_raw("signal A"))
        pipe._check_duplicate_candidate(_raw("signal B"))

    dupes = [r for r in logs if r["event"] == "pipeline.duplicate_candidate"]
    assert dupes == []


def test_duplicate_candidate_silent_across_chats() -> None:
    """Same text in two *different* chats must not cross-trigger — each
    chat owns its own fingerprint LRU."""
    pipe = _make_pipeline()

    with capture_logs() as logs:
        pipe._check_duplicate_candidate(_raw("CALL EURUSD 1m", chat_id=-1001))
        pipe._check_duplicate_candidate(_raw("CALL EURUSD 1m", chat_id=-1002))

    assert [r for r in logs if r["event"] == "pipeline.duplicate_candidate"] == []


def test_duplicate_candidate_fingerprint_lru_is_bounded() -> None:
    """The per-chat fingerprint dict must not grow unboundedly under a
    chatty channel; ``_DUPLICATE_FINGERPRINT_CAP`` controls the bound."""
    from autotrader.services.pipeline import Pipeline  # noqa: PLC0415
    pipe = _make_pipeline()
    cap = Pipeline._DUPLICATE_FINGERPRINT_CAP
    for i in range(cap + 50):
        pipe._check_duplicate_candidate(_raw(f"msg-{i}"))
    assert len(pipe._recent_fingerprints[-1001]) == cap


# ---------------------------------------------------------------------------
# broker.lock.contention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_lock_contention_logs_when_wait_exceeds_threshold() -> None:
    """When one coroutine holds ``_lock`` longer than
    ``_LOCK_CONTENTION_THRESHOLD_SECONDS``, a second coroutine waiting
    on the same lock must emit ``broker.lock.contention`` with its
    ``site`` label so the operator knows which lock-holder was slow.
    """
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()
    # Shrink the threshold to keep the test fast — semantics unchanged.
    mgr._LOCK_CONTENTION_THRESHOLD_SECONDS = 0.05  # type: ignore[misc]

    holder_done = asyncio.Event()

    async def _holder() -> None:
        async with mgr._timed_lock("holder"):
            await asyncio.sleep(0.1)
            holder_done.set()

    async def _waiter() -> None:
        # Give holder a moment to take the lock first.
        await asyncio.sleep(0.01)
        with capture_logs() as logs:
            async with mgr._timed_lock("waiter"):
                pass
        # Stash for outer assert.
        _waiter.captured = logs  # type: ignore[attr-defined]

    await asyncio.gather(_holder(), _waiter())
    assert holder_done.is_set()
    logs = _waiter.captured  # type: ignore[attr-defined]
    contentions = [r for r in logs if r["event"] == "broker.lock.contention"]
    assert len(contentions) == 1, logs
    assert contentions[0]["site"] == "waiter"
    assert contentions[0]["waited_ms"] >= 50


@pytest.mark.asyncio
async def test_broker_lock_contention_silent_when_fast() -> None:
    """An uncontended acquire must NOT emit the warning — Phase 0 must
    not flood logs in steady-state."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    mgr = QuotexManager()
    with capture_logs() as logs:
        async with mgr._timed_lock("fast"):
            pass
    assert [r for r in logs if r["event"] == "broker.lock.contention"] == []


# ---------------------------------------------------------------------------
# aggregator.window_extended
# ---------------------------------------------------------------------------


def test_aggregator_window_extended_logs_after_first_window() -> None:
    """When a buffer has been alive longer than ``window_seconds`` due
    to message-driven extension, the aggregator must emit
    ``aggregator.window_extended`` on every subsequent feed. This is
    the H3 canary — buffer keeps absorbing late chatter today; Phase
    3b will cap it."""
    from autotrader.services.parsers import (  # noqa: PLC0415
        Aggregator,
        ParseError,
        ParseOutcome,
        Parser,
        RawMessage,
    )

    class _NeverParse(Parser):
        @property
        def parser_id(self) -> str:
            return "never"

        def parse(self, messages: list[RawMessage]) -> ParseOutcome:
            return ParseError(reason="never")

    agg = Aggregator(_NeverParse(), window_seconds=10)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    # First message at T=0 opens the buffer.
    agg.feed(RawMessage(text="m1", chat_id=-1001, sender_id=1, received_at=now))
    # Second message at T=5s extends ``expires_at`` but stays inside
    # the window — should NOT log yet.
    with capture_logs() as logs1:
        agg.feed(
            RawMessage(
                text="m2", chat_id=-1001, sender_id=1,
                received_at=now + timedelta(seconds=5),
            ),
        )
    assert [r for r in logs1 if r["event"] == "aggregator.window_extended"] == []

    # Third message at T=15s — buffer has been alive longer than the
    # 10 s window. SHOULD log.
    with capture_logs() as logs2:
        agg.feed(
            RawMessage(
                text="m3", chat_id=-1001, sender_id=1,
                received_at=now + timedelta(seconds=15),
            ),
        )
    extended = [r for r in logs2 if r["event"] == "aggregator.window_extended"]
    assert len(extended) == 1, logs2
    assert extended[0]["chat_id"] == -1001
    assert extended[0]["age_seconds"] >= 15
    assert extended[0]["window_seconds"] == 10
    assert extended[0]["buffered_messages"] == 2


# ---------------------------------------------------------------------------
# feed.ws.auth_rejected
# ---------------------------------------------------------------------------


def test_feed_ws_logs_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ``?token=`` must emit ``feed.ws.auth_rejected`` with
    ``reason='no_token'`` BEFORE the close — operators can correlate
    bad-token reports to the log timeline.

    Uses the ``monkeypatch`` fixture (not an ad-hoc ``MonkeyPatch()``
    instance) so the Quotex stub gets restored after the test — without
    this, ``autotrader.services.quotex_manager.Quotex`` stays patched
    into every subsequent test that imports the manager module.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", FakeQuotex,
    )

    from autotrader.main import app  # noqa: PLC0415

    with capture_logs() as logs, TestClient(app) as c:
        try:
            with c.websocket_connect("/feed/ws"):
                pass
        except Exception:
            # The connect raises because the server closes with 1008.
            pass

    rejects = [r for r in logs if r["event"] == "feed.ws.auth_rejected"]
    assert rejects, logs
    assert rejects[0]["reason"] == "no_token"


def test_feed_ws_logs_when_token_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bogus token must emit ``feed.ws.auth_rejected`` with the
    exception class name as the reason — the bare ``except Exception``
    in feed.py used to silently close with no trace (M4)."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from tests.test_broker import FakeQuotex  # noqa: PLC0415

    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex", FakeQuotex,
    )

    from autotrader.main import app  # noqa: PLC0415

    with capture_logs() as logs, TestClient(app) as c:
        try:
            with c.websocket_connect("/feed/ws?token=garbage"):
                pass
        except Exception:
            pass

    rejects = [r for r in logs if r["event"] == "feed.ws.auth_rejected"]
    assert rejects, logs
    # The exception name depends on Fernet's surface — assert it's
    # set to *something* truthy and not the no-token sentinel.
    assert rejects[0]["reason"] != "no_token"
    assert "detail" in rejects[0]
