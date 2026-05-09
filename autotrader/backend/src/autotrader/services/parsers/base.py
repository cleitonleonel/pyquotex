"""Parser primitives.

The parser layer is intentionally narrow: a ``Parser`` consumes one or
more raw messages and produces a single :class:`ParsedSignal` (or
``None`` when nothing actionable was found).

Higher layers — the multi-message aggregator (``aggregator.py``), the
factory that builds a parser from a per-channel config
(``factory.py``), and the live execution pipeline in Phase 4 — all
talk to this same minimal surface so swapping or adding parser types
later is a one-file affair.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Direction = Literal["call", "put"]


@dataclass(frozen=True, slots=True)
class RawMessage:
    """Trimmed projection of a Telegram message — what the parser cares about."""

    text: str
    chat_id: int = 0
    sender_id: int = 0
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ParsedSignal:
    """Output of a parser. Routed onward to the risk gate + executor."""

    asset: str                        # broker-side code, e.g. "EURUSD"
    direction: Direction              # "call" | "put"
    duration_seconds: int             # expiry window
    stake: float | None = None        # None => default_stake from config
    fire_at: datetime | None = None   # None => fire immediately (live trade)
    raw_text: str = ""
    parser_id: str = ""               # which parser produced this (for logs)
    matched_groups: dict[str, str] = field(default_factory=dict)
    # Asset-resolution diagnostics: raw form the parser saw, and the
    # path that produced ``asset`` ("alias" | "exact" | "otc" | "fallback").
    # Useful for the live-tester UI; ``""`` for the raw means
    # "no resolution metadata" (older parsers).
    asset_raw: str = ""
    asset_via: str = ""
    # True when the executor has *synthesised* this signal as a
    # martingale auto-recovery for a previously-lost trade. The risk
    # gate uses this to bypass the parser's ``trade_mode=scheduled``
    # pin: the original schedule is past, the recovery is meant to
    # fire ASAP. Channel-emitted signals always leave this False.
    is_auto_recovery: bool = False


@dataclass(frozen=True, slots=True)
class ParseError:
    """Returned by parsers when input doesn't match.

    Carrying the *reason* explicitly is the difference between a quiet
    no-match and a misconfigured pattern that should surface in the
    live tester UI.
    """

    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


ParseOutcome = ParsedSignal | ParseError


class Parser(ABC):
    """Stateless single-shot parser. Implementations receive one or
    more messages and decide whether they form a signal.

    For multi-message (aggregating) sources, wrap a stateless parser in
    :class:`autotrader.services.parsers.aggregator.Aggregator`.
    For *one message → many signals* sources (scheduled-batch
    channels), use :class:`autotrader.services.parsers.batch.BatchParser`.
    """

    @property
    @abstractmethod
    def parser_id(self) -> str:
        """Stable identifier for logs / observability."""

    @abstractmethod
    def parse(self, messages: list[RawMessage]) -> ParseOutcome:
        """Try to extract a signal from the given messages.

        Implementations should return ``ParseError`` (not raise) for
        ordinary no-match cases — exceptions are reserved for genuinely
        broken parsers (bad regex compile, etc.) and bubble up to the
        caller.
        """

    def parse_all(self, messages: list[RawMessage]) -> list[ParseOutcome]:
        """Return *every* signal extractable from the messages.

        Default behaviour wraps :meth:`parse` so single-shot parsers
        produce ``[result]``. Batch parsers (one message → many
        signals) override this to return one entry per row.
        """
        return [self.parse(messages)]
