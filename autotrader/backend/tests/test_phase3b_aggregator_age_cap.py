"""Phase 3b aggregator age-cap tests (audit 2026-05-13, H3).

Before this commit a chatty channel could keep an Aggregator buffer
alive indefinitely because ``buf.expires_at = now + window`` was
unconditional on every ``feed()``. A late, unrelated message could
then merge with stale earlier context and emit a corrupted signal.

The Phase 3b fix caps absolute lifetime at
``first_seen_at + window * max_age_multiplier``. These tests prove:

* the cap exists and is configurable;
* a buffer that crosses the cap is dropped on the next ``feed``,
  starting a fresh window for the new message;
* ``expires_at`` never extends past the absolute deadline even when
  the next message arrives well before the cap;
* the Phase 0 ``aggregator.window_extended`` log keeps firing in
  the soft-warning zone between ``window`` and the cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs


def test_buffer_aged_out_drops_and_logs() -> None:
    """A "chatty channel" — messages arriving faster than
    ``window_seconds`` — keeps the buffer alive past the configured
    window today. Phase 3b caps absolute lifetime at
    ``first_seen_at + window * max_age_multiplier``: once the cap is
    crossed, the next feed drops the old buffer (with
    ``aggregator.buffer_aged_out``) and starts a fresh window."""
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

    # window=10s, cap=30s (3×). Simulate a chatty channel by feeding
    # one message every 5s (faster than the window) so eviction never
    # kicks in via the natural ``expires_at`` path — only via the cap.
    agg = Aggregator(_NeverParse(), window_seconds=10, max_age_multiplier=3)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    for sec in (0, 5, 10, 15, 20, 25):
        agg.feed(
            RawMessage(
                text=f"m@{sec}", chat_id=-1, sender_id=1,
                received_at=now + timedelta(seconds=sec),
            ),
        )
    # Pre-cap: all six messages survive in one buffer.
    assert agg.buffer_size() == 6

    # T=35s — past the 30s absolute cap. Old buffer dropped, fresh
    # one starts.
    with capture_logs() as logs:
        agg.feed(
            RawMessage(
                text="m@35", chat_id=-1, sender_id=1,
                received_at=now + timedelta(seconds=35),
            ),
        )

    aged = [r for r in logs if r["event"] == "aggregator.buffer_aged_out"]
    assert len(aged) == 1, logs
    assert aged[0]["age_seconds"] >= 35
    assert aged[0]["max_age_seconds"] == 30
    assert aged[0]["buffered_messages"] == 6

    # Fresh buffer holds only the new message.
    assert agg.buffer_size() == 1


def test_expires_at_clamped_at_absolute_deadline() -> None:
    """When the next ``feed`` arrives BEFORE the cap, ``expires_at`` is
    set to the min of (now + window, first_seen + cap) — i.e. once the
    cap is approached, the window can no longer slide beyond it.
    """
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

    agg = Aggregator(_NeverParse(), window_seconds=10, max_age_multiplier=2)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # T=0: open the buffer. Cap = first_seen + 20s.
    agg.feed(RawMessage(text="m1", chat_id=-2, sender_id=1, received_at=now))
    # T=5: keep buffer alive (within window).
    agg.feed(
        RawMessage(
            text="m2", chat_id=-2, sender_id=1,
            received_at=now + timedelta(seconds=5),
        ),
    )

    # T=15: now + window would be T=25, but the cap is T=20. Clamp wins.
    agg.feed(
        RawMessage(
            text="m3", chat_id=-2, sender_id=1,
            received_at=now + timedelta(seconds=15),
        ),
    )

    buf = agg._buffers[(-2, 1)]
    assert buf.expires_at <= now + timedelta(seconds=20), (
        "expires_at must not extend past first_seen_at + max_total_age"
    )


def test_window_extended_warning_still_fires_in_soft_zone() -> None:
    """The Phase 0 ``aggregator.window_extended`` log still fires while
    the buffer is alive but past ``window_seconds`` (and inside the
    cap). Phase 3b is the gate; Phase 0 is the observability."""
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

    agg = Aggregator(_NeverParse(), window_seconds=5, max_age_multiplier=4)
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    # Keep the buffer alive by feeding within ``window_seconds``:
    # T=0 (open), T=3 (still inside window), T=6 (past window, inside cap).
    agg.feed(RawMessage(text="m1", chat_id=-3, sender_id=1, received_at=now))
    agg.feed(
        RawMessage(
            text="m2", chat_id=-3, sender_id=1,
            received_at=now + timedelta(seconds=3),
        ),
    )

    with capture_logs() as logs:
        # T=6s — past window (5s) but inside cap (20s).
        agg.feed(
            RawMessage(
                text="m3", chat_id=-3, sender_id=1,
                received_at=now + timedelta(seconds=6),
            ),
        )

    extended = [r for r in logs if r["event"] == "aggregator.window_extended"]
    assert len(extended) == 1, logs
    # No buffer_aged_out yet — that fires only past the cap.
    assert [r for r in logs if r["event"] == "aggregator.buffer_aged_out"] == []


def test_max_age_multiplier_validation() -> None:
    """``max_age_multiplier < 1`` is rejected — the cap must be at
    least as long as a single sliding window."""
    from autotrader.services.parsers import (  # noqa: PLC0415
        Aggregator,
        ParseError,
        ParseOutcome,
        Parser,
        RawMessage,
    )

    class _Trivial(Parser):
        @property
        def parser_id(self) -> str:
            return "trivial"

        def parse(self, messages: list[RawMessage]) -> ParseOutcome:
            return ParseError(reason="trivial")

    with pytest.raises(ValueError, match="max_age_multiplier"):
        Aggregator(_Trivial(), window_seconds=10, max_age_multiplier=0)
