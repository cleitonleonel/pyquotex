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
# Phase 3b age cap (audit 2026-05-13, H3): absolute upper bound on
# buffer lifetime regardless of how chatty the channel is. The
# Aggregator's per-message ``expires_at = now + window`` extends the
# sliding window indefinitely under a chatty channel — a late, unrelated
# message can then re-trigger the inner parser against ageing context
# and emit a corrupted signal. Default 3× the configured window is
# generous: legitimate two- and three-part signals fit comfortably
# inside ``window_seconds``, so 3× covers retries and Telegram
# delivery jitter without holding onto material that's plainly stale.
_DEFAULT_MAX_AGE_MULTIPLIER: Final = 3


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
        max_age_multiplier: int = _DEFAULT_MAX_AGE_MULTIPLIER,
    ) -> None:
        if window_seconds <= 0:
            msg = "window_seconds must be positive"
            raise ValueError(msg)
        if max_age_multiplier < 1:
            msg = "max_age_multiplier must be >= 1"
            raise ValueError(msg)
        self._inner = inner
        self._window = timedelta(seconds=window_seconds)
        # Phase 3b (audit 2026-05-13, H3): absolute lifetime cap. A
        # buffer can have its ``expires_at`` extended by chatter, but
        # never past ``first_seen_at + _max_total_age``. When the cap
        # is hit the buffer is dropped at the next ``feed`` and the
        # new message starts a fresh buffer.
        self._max_total_age = self._window * max_age_multiplier
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
        # Phase 3b age-cap (audit 2026-05-13, H3): check the absolute
        # cap on THIS chat's buffer BEFORE the general expiry sweep,
        # so we can emit the distinctive ``aggregator.buffer_aged_out``
        # log line (vs the silent natural expiry). Once the cap is
        # crossed the buffer is dropped and the new message starts a
        # fresh window — preventing a chatty channel from holding
        # stale context indefinitely. ``_evict_expired`` below then
        # cleans up any other stale per-sender buffers.
        key = (message.chat_id, message.sender_id)
        buf = self._buffers.get(key)
        if buf is not None and (now - buf.first_seen_at) >= self._max_total_age:
            log.warning(
                "aggregator.buffer_aged_out",
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                age_seconds=round((now - buf.first_seen_at).total_seconds(), 3),
                max_age_seconds=int(self._max_total_age.total_seconds()),
                buffered_messages=len(buf.messages),
            )
            del self._buffers[key]
            buf = None
        self._evict_expired(now)
        # ``_evict_expired`` may have removed this key too if its
        # ``expires_at`` clamp landed before ``now``. Re-resolve so
        # we open a fresh buffer for the current message.
        if buf is not None and key not in self._buffers:
            buf = None
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
            # — Phase 3b's hard cap above is the eventual gate.
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
        # Phase 3b: extend ``expires_at`` but never past the absolute
        # cap. After ``first_seen_at + _max_total_age`` the buffer
        # is dead — the next ``feed`` for this key starts fresh
        # (handled above), and ``_evict_expired`` will sweep dead
        # buffers between feeds too.
        absolute_deadline = buf.first_seen_at + self._max_total_age
        buf.expires_at = min(now + self._window, absolute_deadline)
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
