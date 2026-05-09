"""One-message, many-signals parser.

Some channels post a *batch* of scheduled signals in a single
message::

    DATE: 07.05.2026
    TIMEZONE : UTC/GMT (+06:00)
    FUTURE SIGNALS 🕯
    📏📏📏📏📏📏📏📏📏📏

    01:51 USDBDT-OTC PUT
    01:53 USDBDT-OTC PUT
    01:55 USDBDT-OTC PUT
    01:58 USDBDT-OTC CALL

    📏📏📏📏📏📏📏📏📏📏

We extract *every* row as a separate :class:`ParsedSignal`, each with
a ``fire_at`` UTC datetime computed from the channel-side ``DATE`` +
``TIME`` + ``TIMEZONE``. The header regex is optional — channels that
post the date / timezone elsewhere (or always trade today UTC) just
configure a row regex.

Required row groups:

    time       e.g. ``01:53``
    asset      e.g. ``USDBDT-OTC``
    direction  e.g. ``PUT`` (resolved via the direction normaliser)

Optional row groups:

    duration   per-row override of the per-channel default
    stake      per-row stake override

Optional header groups (all extracted from the first match in the
text):

    date       common formats: ``DD.MM.YYYY``, ``YYYY-MM-DD``,
               ``DD/MM/YYYY``, ``MM/DD/YYYY``, ``DD-MM-YYYY``
    tz_offset  ``+06:00`` / ``-0500`` / ``+6`` — sign + HH(:MM)
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
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
    normalise_direction,
    normalise_duration,
)
from autotrader.services.parsers.template import template_to_regex

PhaseKind = str  # "template" | "regex"

_REQUIRED_ROW_GROUPS: Final = ("time", "asset", "direction")
_KNOWN_ROW_GROUPS: Final = (*_REQUIRED_ROW_GROUPS, "duration", "stake")

_DATE_FORMATS: Final = (
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%y",
)

_TZ_OFFSET_RE: Final = re.compile(r"^([+-])(\d{1,2})(?::?(\d{2}))?$")
_TIME_RE: Final = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def _parse_date(raw: str) -> date | None:
    s = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_tz_offset(raw: str) -> int | None:
    """Return offset in minutes east of UTC, or None.

    Accepts ``"+06:00"``, ``"-0500"``, ``"+6"``, ``"+06"``, etc.
    """
    s = raw.strip().replace(" ", "")
    if not s:
        return None
    m = _TZ_OFFSET_RE.match(s)
    if m is None:
        return None
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or 0)
    if hours > 14 or minutes >= 60:
        return None
    return sign * (hours * 60 + minutes)


def _combine(d: date, t: time, tz_offset_minutes: int) -> datetime:
    """Build a UTC datetime from a channel-local date+time."""
    naive = datetime.combine(d, t)
    return (naive - timedelta(minutes=tz_offset_minutes)).replace(tzinfo=UTC)


class BatchParser(Parser):
    """Extracts every scheduled signal in a batch message."""

    def __init__(
        self,
        *,
        row_pattern: str,
        header_pattern: str | None = None,
        row_kind: PhaseKind = "regex",
        header_kind: PhaseKind = "regex",
        timezone_offset_minutes: int = 0,
        asset_aliases: dict[str, str] | None = None,
        known_assets: tuple[str, ...] | None = None,
        default_duration_seconds: int = 60,
        default_duration_unit: str = "m",
        parser_id: str = "batch",
    ) -> None:
        self._row_re = _compile_phase("row", row_pattern, row_kind)
        missing = [g for g in _REQUIRED_ROW_GROUPS if g not in self._row_re.groupindex]
        if missing:
            msg = f"row regex missing required named group(s): {', '.join(missing)}"
            raise ValueError(msg)

        self._header_re: re.Pattern[str] | None = None
        if header_pattern and header_pattern.strip():
            self._header_re = _compile_phase("header", header_pattern, header_kind)

        self._tz_offset_default = timezone_offset_minutes
        self._aliases = asset_aliases or {}
        self._known_assets = known_assets or ()
        self._default_duration = default_duration_seconds
        self._default_duration_unit = default_duration_unit
        self._id = parser_id

    @property
    def parser_id(self) -> str:
        return self._id

    # ------------------------------------------------------------------
    # Single-shot Parser interface — returns the first row (or error)
    # ------------------------------------------------------------------

    def parse(self, messages: list[RawMessage]) -> ParseOutcome:
        results = self.parse_all(messages)
        for r in results:
            if isinstance(r, ParsedSignal):
                return r
        return results[0]  # an error, by construction

    def parse_all(self, messages: list[RawMessage]) -> list[ParseOutcome]:
        if not messages:
            return [ParseError(reason="no messages")]

        text = "\n".join(m.text for m in messages)
        date_obj, tz_offset = self._extract_header(text)
        if date_obj is None:
            # No header date → schedule for today (in the channel TZ).
            now_utc = messages[-1].received_at or datetime.now(UTC)
            local_now = now_utc + timedelta(minutes=tz_offset)
            date_obj = local_now.date()

        out: list[ParseOutcome] = []
        for match in self._row_re.finditer(text):
            outcome = self._build_signal(match, date_obj, tz_offset, raw_text=text)
            out.append(outcome)

        if not out:
            return [ParseError(reason="no rows matched", detail={"text_preview": text[:200]})]
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_header(self, text: str) -> tuple[date | None, int]:
        if self._header_re is None:
            return None, self._tz_offset_default
        m = self._header_re.search(text)
        if m is None:
            return None, self._tz_offset_default

        groups = {k: v for k, v in m.groupdict().items() if v is not None}
        date_obj: date | None = None
        if "date" in groups:
            date_obj = _parse_date(groups["date"])

        tz_offset = self._tz_offset_default
        if "tz_offset" in groups:
            parsed = _parse_tz_offset(groups["tz_offset"])
            if parsed is not None:
                tz_offset = parsed

        return date_obj, tz_offset

    def _build_signal(  # noqa: PLR0911  (one return per row-validation step)
        self,
        match: re.Match[str],
        date_obj: date,
        tz_offset_minutes: int,
        *,
        raw_text: str,
    ) -> ParseOutcome:
        groups = {k: v for k, v in match.groupdict().items() if v is not None}

        direction = normalise_direction(groups.get("direction", ""))
        if direction is None:
            return ParseError(
                reason="unrecognised direction in batch row",
                detail={"row": match.group(0), "groups": groups},
            )

        time_match = _TIME_RE.match(groups.get("time", "").strip())
        if time_match is None:
            return ParseError(
                reason="unparseable time in batch row",
                detail={"row": match.group(0), "raw": groups.get("time")},
            )
        h = int(time_match.group(1))
        mi = int(time_match.group(2))
        sec = int(time_match.group(3) or 0)
        if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= sec < 60):
            return ParseError(
                reason="time out of range in batch row",
                detail={"row": match.group(0), "raw": groups.get("time")},
            )
        fire_at = _combine(date_obj, time(h, mi, sec), tz_offset_minutes)

        resolution = resolve_asset(
            groups.get("asset", ""),
            aliases=self._aliases,
            known_assets=self._known_assets,
        )
        if not resolution.code:
            return ParseError(
                reason="empty asset in batch row",
                detail={"row": match.group(0), "groups": groups},
            )

        duration = self._default_duration
        if "duration" in groups:
            parsed = normalise_duration(
                groups["duration"], default_unit=self._default_duration_unit,
            )
            if parsed is None:
                return ParseError(
                    reason="unparseable duration in batch row",
                    detail={"row": match.group(0), "raw": groups["duration"]},
                )
            duration = parsed

        stake: float | None = None
        if "stake" in groups:
            try:
                stake = float(groups["stake"].replace(",", ""))
            except ValueError:
                return ParseError(
                    reason="unparseable stake in batch row",
                    detail={"row": match.group(0), "raw": groups["stake"]},
                )

        return ParsedSignal(
            asset=resolution.code,
            direction=direction,
            duration_seconds=duration,
            stake=stake,
            fire_at=fire_at,
            raw_text=raw_text,
            parser_id=self._id,
            matched_groups={k: v for k, v in groups.items() if k in _KNOWN_ROW_GROUPS},
            asset_raw=resolution.raw,
            asset_via=resolution.via,
        )


def _compile_phase(name: str, source: str, kind: PhaseKind) -> re.Pattern[str]:
    """Compile a template or regex for a batch phase."""
    pattern = template_to_regex(source) if kind == "template" else source
    try:
        return re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    except re.error as exc:
        msg = f"invalid {name} regex: {exc}"
        raise ValueError(msg) from exc
