"""Sliding-window message aggregator.

Some channels split a single signal across multiple messages, e.g.::

    EUR/USD          (msg #1)
    BUY              (msg #2)
    expiry 1 min     (msg #3)

The :class:`Aggregator` buffers messages per ``(chat_id, sender_id)``
key, retries the inner parser on every new message, and emits a
:class:`ParsedSignal` the moment a successful parse appears. Buffers
older than ``window_seconds`` are dropped silently — channels with no
multi-message pattern just retain the most recent message.

All state is in-memory and per-process; restarting the API resets the
windows. That's deliberate — half-built signals straddling a restart
are usually stale.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog

from autotrader.services.parsers.base import (
    ParsedSignal,
    ParseError,
    ParseOutcome,
    Parser,
    RawMessage,
)

log = structlog.get_logger(__name__)

_DEFAULT_MAX_BUFFERS: Final = 1024  # bound memory: keys evicted LRU-style


@dataclass(slots=True)
class _Buffer:
    messages: list[RawMessage]
    expires_at: datetime
    # Phase 0 instrumentation (audit 2026-05-13, H3): the timestamp of
    # the message that opened this buffer. Used to detect "window has
    # been extended past the configured ``window_seconds``" — a chatty
    # channel can keep the buffer alive indefinitely today because
    # ``expires_at`` is reset on every ``feed()``. Phase 3b will cap
    # the buffer at ``first_seen_at + window * 3``; for Phase 0 we
    # just log when the extension happens so we can measure it.
    first_seen_at: datetime


class Aggregator:
    """Wraps a stateless parser with a per-sender sliding window."""

    def __init__(
        self,
        inner: Parser,
        *,
        window_seconds: int,
        max_buffers: int = _DEFAULT_MAX_BUFFERS,
        max_buffered_messages: int = 16,
    ) -> None:
        if window_seconds <= 0:
            msg = "window_seconds must be positive"
            raise ValueError(msg)
        self._inner = inner
        self._window = timedelta(seconds=window_seconds)
        self._max_buffers = max_buffers
        self._max_msgs = max_buffered_messages
        # OrderedDict so we can evict in insertion order when the cap
        # is reached.
        self._buffers: OrderedDict[tuple[int, int], _Buffer] = OrderedDict()

    @property
    def parser_id(self) -> str:
        return f"aggregator({self._inner.parser_id})"

    def feed(self, message: RawMessage) -> ParseOutcome:
        """Add a message and try to parse the buffered run.

        Returns:
            * :class:`ParsedSignal` — buffer parsed, and is now cleared.
            * :class:`ParseError`  — nothing emitted yet (still buffering).
        """
        now = message.received_at if message.received_at else datetime.now(UTC)
        self._evict_expired(now)

        key = (message.chat_id, message.sender_id)
        buf = self._buffers.get(key)
        if buf is None:
            buf = _Buffer(
                messages=[],
                expires_at=now + self._window,
                first_seen_at=now,
            )
            self._buffers[key] = buf
        else:
            # Keep this key as MRU.
            self._buffers.move_to_end(key)
            # Phase 0 instrumentation: the buffer has been alive longer
            # than the configured window because earlier messages kept
            # pushing ``expires_at`` forward. Logged at WARNING so a
            # chatty channel surfaces during the observation window
            # — Phase 3b will cap this at ``first_seen_at + window``.
            age = now - buf.first_seen_at
            if age >= self._window:
                log.warning(
                    "aggregator.window_extended",
                    chat_id=message.chat_id,
                    sender_id=message.sender_id,
                    age_seconds=round(age.total_seconds(), 3),
                    window_seconds=int(self._window.total_seconds()),
                    buffered_messages=len(buf.messages),
                )

        buf.messages.append(message)
        buf.expires_at = now + self._window
        if len(buf.messages) > self._max_msgs:
            # Drop the oldest to keep the buffer bounded.
            buf.messages = buf.messages[-self._max_msgs :]

        # Bound total buffers (LRU-style). Eviction can drop a stale
        # mid-build buffer but that's preferable to unbounded growth.
        while len(self._buffers) > self._max_buffers:
            self._buffers.popitem(last=False)

        outcome = self._inner.parse(buf.messages)
        if isinstance(outcome, ParsedSignal):
            del self._buffers[key]
            return outcome
        return outcome

    def _evict_expired(self, now: datetime) -> None:
        # Iterate over a snapshot — del while iterating is unsafe.
        for key in [k for k, v in self._buffers.items() if v.expires_at < now]:
            del self._buffers[key]

    def buffer_size(self) -> int:
        """Diagnostic: total messages currently buffered."""
        return sum(len(b.messages) for b in self._buffers.values())

    def clear(self) -> None:
        """Drop every buffer (e.g. on logout / parser config change)."""
        self._buffers.clear()


def parse_via_aggregator(
    parser: Parser,
    messages: list[RawMessage],
    *,
    window_seconds: int,
) -> ParseOutcome:
    """One-shot: feed all messages through a fresh :class:`Aggregator`.

    Used by the live-tester endpoint where we receive a list of
    messages all at once but want the aggregator semantics applied.
    """
    agg = Aggregator(parser, window_seconds=window_seconds)
    last: ParseOutcome = ParseError(reason="no messages")
    for m in messages:
        last = agg.feed(m)
        if isinstance(last, ParsedSignal):
            return last
    return last
