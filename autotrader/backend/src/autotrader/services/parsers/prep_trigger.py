"""Two-phase prep+trigger parser.

A different multi-message model from the sliding-window concat
``Aggregator``. Many signal channels (EliteClassBinary,
GoldRunFX, …) post a *prep* message followed by a *trigger*:

    Message 1 (prep)    "🌐 PAIR: USD-NGN OTC   ⏱ TIME: 01 Minute"
    Message 2 (trigger) [👍 sticker]   ← direction only

Concat-style aggregation works in theory but the regex gets brittle
because the trigger is a single emoji. ``PrepTriggerParser`` keeps the
two phases conceptually separate:

* the *prep* regex extracts asset / duration / stake / fire_at;
* the *trigger* regex extracts direction and (re-)confirms the
  signal exists;
* the parser holds a per-chat *pending* prep until the next trigger
  arrives, then emits a :class:`ParsedSignal` immediately. No
  windowing wait.

Stale preps (no trigger within ``gap_seconds``) are dropped silently.
Restarting the API resets pending preps — same trade-off as the
concat aggregator.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from autotrader.services.parsers.asset_resolver import AssetResolution, resolve_asset
from autotrader.services.parsers.base import (
    ParsedSignal,
    ParseError,
    ParseOutcome,
    Parser,
    RawMessage,
)
from autotrader.services.parsers.normalize import (
    normalise_direction,
    normalise_duration,
    normalise_fire_at,
)
from autotrader.services.parsers.template import template_to_regex

PhaseKind = Literal["template", "regex"]
_DEFAULT_MAX_PENDING: Final = 1024


@dataclass(slots=True)
class _PendingPrep:
    """Stored prep state waiting for a trigger."""

    asset: str
    asset_raw: str
    asset_via: str
    duration_seconds: int
    stake: float | None
    fire_at: datetime | None
    raw_text: str
    matched_groups: dict[str, str]
    expires_at: datetime


class PrepTriggerParser(Parser):
    """Stateful prep+trigger parser.

    ``parse(messages)`` feeds each message sequentially through the
    state machine and returns the last-emitted outcome — handy for the
    live tester. The live executor (Phase 4) calls :meth:`feed`
    directly on each incoming message instead.
    """

    def __init__(
        self,
        *,
        prep: str,
        trigger: str,
        prep_kind: PhaseKind = "template",
        trigger_kind: PhaseKind = "template",
        gap_seconds: int = 120,
        timezone_offset_minutes: int = 0,
        asset_aliases: dict[str, str] | None = None,
        known_assets: tuple[str, ...] | None = None,
        default_duration_seconds: int = 60,
        default_duration_unit: str = "m",
        max_pending: int = _DEFAULT_MAX_PENDING,
        parser_id: str = "prep_trigger",
    ) -> None:
        if gap_seconds <= 0:
            msg = "gap_seconds must be positive"
            raise ValueError(msg)

        self._prep_re = _compile_phase("prep", prep, prep_kind)
        self._trigger_re = _compile_phase("trigger", trigger, trigger_kind)

        # Up-front validation of required groups so config errors fail
        # at build-time, not when the channel posts a message.
        if "asset" not in self._prep_re.groupindex:
            msg = "prep must contain a named group 'asset' (or {ASSET} placeholder)"
            raise ValueError(msg)
        if "direction" not in self._trigger_re.groupindex:
            msg = "trigger must contain a named group 'direction' (or {DIRECTION} placeholder)"
            raise ValueError(msg)

        self._gap = timedelta(seconds=gap_seconds)
        self._tz_offset = timezone_offset_minutes
        self._aliases = asset_aliases or {}
        self._known_assets = known_assets or ()
        self._default_duration = default_duration_seconds
        self._default_duration_unit = default_duration_unit
        self._max_pending = max_pending
        self._id = parser_id
        self._pending: OrderedDict[int, _PendingPrep] = OrderedDict()

    @property
    def parser_id(self) -> str:
        return self._id

    # ------------------------------------------------------------------
    # Stateful per-message API (used by the live executor)
    # ------------------------------------------------------------------

    def feed(self, message: RawMessage) -> ParseOutcome:
        """Process a single message; return outcome.

        * ``ParsedSignal`` — trigger fired and the buffered prep was
          combined with it. The pending entry for this chat is gone.
        * ``ParseError`` — message was a prep (stored, awaiting
          trigger), an unrelated message, or a trigger with no recent
          prep.
        """
        now = message.received_at if message.received_at else datetime.now(UTC)
        self._evict_expired(now)

        prep_match = self._prep_re.search(message.text)
        if prep_match is not None:
            return self._store_prep(message, now, prep_match)

        trigger_match = self._trigger_re.search(message.text)
        if trigger_match is not None:
            return self._fire_trigger(message, trigger_match)

        return ParseError(
            reason="no prep or trigger match",
            detail={"text_preview": message.text[:200]},
        )

    def parse(self, messages: list[RawMessage]) -> ParseOutcome:
        """Sequence-friendly wrapper for the live tester.

        Feeds each message in order; returns the first emitted signal,
        otherwise the final ``ParseError`` (which carries the most
        specific reason — e.g. "trigger received but no recent prep").
        """
        if not messages:
            return ParseError(reason="no messages")
        last: ParseOutcome = ParseError(reason="no messages")
        for m in messages:
            last = self.feed(m)
            if isinstance(last, ParsedSignal):
                return last
        return last

    def clear(self) -> None:
        """Drop every pending prep (e.g. on logout / config change)."""
        self._pending.clear()

    def pending_size(self) -> int:
        """Diagnostic: how many chats have a stored prep right now."""
        return len(self._pending)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _store_prep(
        self,
        message: RawMessage,
        now: datetime,
        match: re.Match[str],
    ) -> ParseOutcome:
        groups = {k: v for k, v in match.groupdict().items() if v is not None}
        raw_asset = groups.get("asset", "")
        resolution: AssetResolution = resolve_asset(
            raw_asset,
            aliases=self._aliases,
            known_assets=self._known_assets,
        )
        if not resolution.code:
            return ParseError(reason="empty asset on prep", detail={"groups": groups})

        duration = self._default_duration
        if "duration" in groups:
            parsed = normalise_duration(
                groups["duration"], default_unit=self._default_duration_unit,
            )
            if parsed is None:
                return ParseError(
                    reason="unparseable duration on prep",
                    detail={"raw": groups["duration"]},
                )
            duration = parsed

        stake: float | None = None
        if "stake" in groups:
            try:
                stake = float(groups["stake"].replace(",", ""))
            except ValueError:
                return ParseError(
                    reason="unparseable stake on prep",
                    detail={"raw": groups["stake"]},
                )

        fire_at: datetime | None = None
        if "fire_at" in groups:
            fire_at = normalise_fire_at(
                groups["fire_at"],
                now=now,
                tz_offset_minutes=self._tz_offset,
            )

        self._pending[message.chat_id] = _PendingPrep(
            asset=resolution.code,
            asset_raw=resolution.raw,
            asset_via=resolution.via,
            duration_seconds=duration,
            stake=stake,
            fire_at=fire_at,
            raw_text=message.text,
            matched_groups=groups,
            expires_at=now + self._gap,
        )
        # MRU bookkeeping for the cap.
        self._pending.move_to_end(message.chat_id)
        while len(self._pending) > self._max_pending:
            self._pending.popitem(last=False)

        return ParseError(
            reason="prep stored, awaiting trigger",
            detail={"asset": resolution.code, "expires_in_s": self._gap.total_seconds()},
        )

    def _fire_trigger(
        self,
        message: RawMessage,
        match: re.Match[str],
    ) -> ParseOutcome:
        pending = self._pending.get(message.chat_id)
        if pending is None:
            return ParseError(
                reason="trigger received but no recent prep",
                detail={"text_preview": message.text[:80]},
            )

        groups = {k: v for k, v in match.groupdict().items() if v is not None}
        direction = normalise_direction(groups.get("direction", ""))
        if direction is None:
            return ParseError(
                reason="unrecognised direction on trigger",
                detail={"raw": groups.get("direction"), "groups": groups},
            )

        del self._pending[message.chat_id]
        merged_groups = dict(pending.matched_groups)
        merged_groups.update({f"trigger.{k}": v for k, v in groups.items()})

        return ParsedSignal(
            asset=pending.asset,
            direction=direction,
            duration_seconds=pending.duration_seconds,
            stake=pending.stake,
            fire_at=pending.fire_at,
            raw_text=f"{pending.raw_text}\n{message.text}",
            parser_id=self._id,
            matched_groups=merged_groups,
            asset_raw=pending.asset_raw,
            asset_via=pending.asset_via,
        )

    def _evict_expired(self, now: datetime) -> None:
        for key in [k for k, v in self._pending.items() if v.expires_at < now]:
            del self._pending[key]


def _compile_phase(name: str, source: str, kind: PhaseKind) -> re.Pattern[str]:
    """Compile a template or regex for one of the prep/trigger phases."""
    pattern = template_to_regex(source) if kind == "template" else source
    try:
        return re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    except re.error as exc:
        msg = f"invalid {name} regex: {exc}"
        raise ValueError(msg) from exc


def parse_via_prep_trigger(
    parser: PrepTriggerParser,
    messages: list[RawMessage],
) -> ParseOutcome:
    """One-shot helper for the live tester."""
    return parser.parse(messages)
