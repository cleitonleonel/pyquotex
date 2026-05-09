"""Regex parser — power-user mode.

The user provides a Python regex with named groups. Required groups:

    direction   -> matched against the direction normaliser
    asset       -> matched against the asset normaliser

Optional groups:

    duration    -> normalised to seconds (default unit minutes)
    fire_at     -> HH:MM (channel timezone) for scheduled trades.
                   ``time`` is accepted as a synonym since that's the
                   natural name in most signal channels.
    stake       -> numeric override for the per-channel default stake

Anything outside those groups is ignored, which lets users include
emoji, prose, or other noise in their pattern without it leaking
into the structured signal.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final

from autotrader.services.parsers.asset_resolver import resolve_asset
from autotrader.services.parsers.base import (
    ParsedSignal,
    ParseError,
    ParseOutcome,
    Parser,
    RawMessage,
)
from autotrader.services.parsers.normalize import (
    find_duration_in_text,
    normalise_direction,
    normalise_duration,
    normalise_fire_at,
)

_REQUIRED_GROUPS: Final = ("direction", "asset")
_KNOWN_GROUPS: Final = (*_REQUIRED_GROUPS, "duration", "fire_at", "time", "stake")


class RegexParser(Parser):
    """Compile-once, parse-many regex parser."""

    def __init__(
        self,
        pattern: str,
        *,
        flags: int = re.IGNORECASE | re.MULTILINE,
        timezone_offset_minutes: int = 0,
        asset_aliases: dict[str, str] | None = None,
        known_assets: tuple[str, ...] | None = None,
        default_duration_seconds: int = 60,
        default_duration_unit: str = "m",
        parser_id: str = "regex",
    ) -> None:
        try:
            self._regex = re.compile(pattern, flags)
        except re.error as exc:
            msg = f"invalid regex: {exc}"
            raise ValueError(msg) from exc

        missing = [g for g in _REQUIRED_GROUPS if g not in self._regex.groupindex]
        if missing:
            msg = f"regex missing required named group(s): {', '.join(missing)}"
            raise ValueError(msg)

        self._tz_offset = timezone_offset_minutes
        self._aliases = asset_aliases or {}
        self._known_assets = known_assets or ()
        self._default_duration = default_duration_seconds
        self._default_duration_unit = default_duration_unit
        self._id = parser_id

    @property
    def parser_id(self) -> str:
        return self._id

    def parse(self, messages: list[RawMessage]) -> ParseOutcome:  # noqa: PLR0911  (one branch per parse-failure reason)
        if not messages:
            return ParseError(reason="no messages")

        text = "\n".join(m.text for m in messages)
        match = self._regex.search(text)
        if match is None:
            return ParseError(reason="no regex match", detail={"text_preview": text[:200]})

        groups = {k: v for k, v in match.groupdict().items() if v is not None}
        # Validate up-front so we report a single coherent error rather
        # than a half-built signal.
        direction = normalise_direction(groups.get("direction", ""))
        if direction is None:
            return ParseError(
                reason="unrecognised direction",
                detail={"raw": groups.get("direction"), **{"groups": groups}},
            )

        raw_asset = groups.get("asset", "")
        resolution = resolve_asset(
            raw_asset,
            aliases=self._aliases,
            known_assets=self._known_assets,
        )
        asset = resolution.code
        if not asset:
            return ParseError(reason="empty asset", detail={"groups": groups})

        duration = self._default_duration
        if "duration" in groups:
            parsed_dur = normalise_duration(
                groups["duration"], default_unit=self._default_duration_unit,
            )
            if parsed_dur is None:
                return ParseError(
                    reason="unparseable duration",
                    detail={"raw": groups["duration"], "groups": groups},
                )
            duration = parsed_dur
        else:
            # Many channels put the expiration period in a header line
            # the row regex doesn't span (e.g. "5 minute(s) until
            # expiration" above an ``ASSET TIME DIRECTION`` row). Scan
            # the full message as a fallback so the trade fires with
            # the right period rather than the per-config default.
            scanned = find_duration_in_text(
                text, default_unit=self._default_duration_unit,
            )
            if scanned is not None:
                duration = scanned

        stake: float | None = None
        if "stake" in groups:
            try:
                stake = float(groups["stake"].replace(",", ""))
            except ValueError:
                return ParseError(
                    reason="unparseable stake",
                    detail={"raw": groups["stake"], "groups": groups},
                )

        # Accept ``time`` as an alias for ``fire_at`` — that's how
        # most signal channels name the entry-time field, and forcing
        # users to remember our internal jargon is a footgun.
        fire_at_raw = groups.get("fire_at") or groups.get("time")
        fire_at: datetime | None = None
        if fire_at_raw is not None:
            fire_at = normalise_fire_at(
                fire_at_raw,
                now=messages[-1].received_at,
                tz_offset_minutes=self._tz_offset,
            )
            if fire_at is None and fire_at_raw.strip().lower() not in {
                "", "now", "live", "asap",
            }:
                return ParseError(
                    reason="unparseable fire_at",
                    detail={"raw": fire_at_raw, "groups": groups},
                )

        return ParsedSignal(
            asset=asset,
            direction=direction,
            duration_seconds=duration,
            stake=stake,
            fire_at=fire_at,
            raw_text=text,
            parser_id=self._id,
            matched_groups={k: v for k, v in groups.items() if k in _KNOWN_GROUPS},
            asset_raw=resolution.raw,
            asset_via=resolution.via,
        )
