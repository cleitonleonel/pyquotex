"""Direction / duration / asset / time normalisation.

Channels write trades a thousand different ways — emoji, broker-specific
asset codes, duration suffixes mixing minutes and seconds. The parsers
hand off to these helpers so each parser type stays small.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Final

from autotrader.services.parsers.base import Direction

# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------

# Tokens that mean "go up" / call / buy. Matched case-insensitively
# AFTER stripping whitespace; emoji are matched as-is. Order doesn't
# matter — we just check membership.
_CALL_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "buy", "up", "call", "long", "bull", "bullish", "green",
        "🟢", "🟩", "📈", "⬆", "⬆️", "↑", "🔼", "🔝",
        # Thumbs / heart emoji used by prep+sticker channels
        "👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "✅", "💚",
    },
)
_PUT_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "sell", "down", "put", "short", "bear", "bearish", "red",
        "🔴", "🟥", "📉", "⬇", "⬇️", "↓", "🔽",
        "👎", "👎🏻", "👎🏼", "👎🏽", "👎🏾", "👎🏿", "❌", "❤",
    },
)


def normalise_direction(raw: str) -> Direction | None:
    """Return ``"call"`` / ``"put"`` or ``None`` if unrecognised."""
    s = raw.strip().lower()
    # Try the whole token first, then the first non-empty word.
    if s in _CALL_TOKENS:
        return "call"
    if s in _PUT_TOKENS:
        return "put"
    for token in re.split(r"\s+", s):
        if not token:
            continue
        if token in _CALL_TOKENS:
            return "call"
        if token in _PUT_TOKENS:
            return "put"
    return None


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------

_DURATION_RE: Final = re.compile(
    r"""
    ^\s*
    (?P<value>\d+(?:\.\d+)?)            # numeric value
    \s*
    (?P<unit>
        s|sec|secs|second|seconds
      | m|min|mins|minute|minutes
      | h|hr|hrs|hour|hours
    )?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_M_PREFIX_RE: Final = re.compile(r"^\s*[mM](\d+)\s*$")  # "M1", "M5"


def normalise_duration(raw: str, *, default_unit: str = "m") -> int | None:  # noqa: PLR0911
    """Return duration in seconds, or ``None`` if unparseable.

    Accepts ``"60"``, ``"1m"``, ``"1 minute"``, ``"M1"``, ``"5 mins"``,
    etc. ``default_unit`` is used when the raw value carries no
    suffix — most signal channels post bare numbers in minutes, so
    that's the default.
    """
    s = raw.strip()
    if not s:
        return None

    # MetaTrader / TradingView shorthand: M1, M5, M15.
    if (m := _M_PREFIX_RE.match(s)) is not None:
        return int(m.group(1)) * 60

    match = _DURATION_RE.match(s)
    if match is None:
        return None

    value = float(match.group("value"))
    unit = (match.group("unit") or default_unit).lower()
    if unit.startswith("s"):
        return int(value)
    if unit.startswith("m"):
        return int(value * 60)
    if unit.startswith("h"):
        return int(value * 3600)
    return None


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

# Strip non-alphanumeric (keeps the slash-removal explicit) then
# uppercase. Channels write "EUR/USD", "EURUSD", "EUR-USD"; the broker
# always wants "EURUSD".
_ASSET_NOISE_RE: Final = re.compile(r"[^A-Za-z0-9_]")


def normalise_asset(raw: str, aliases: dict[str, str] | None = None) -> str:
    """Apply per-channel aliases first, then strip and uppercase.

    ``aliases`` keys are matched case-insensitively against the *raw*
    input; values are returned verbatim (so users can produce broker-
    specific suffixes like ``"EURUSD_otc"`` if they want).
    """
    s = raw.strip()
    if aliases:
        lookup = {k.casefold(): v for k, v in aliases.items()}
        if (mapped := lookup.get(s.casefold())) is not None:
            return mapped
    return _ASSET_NOISE_RE.sub("", s).upper()


# ---------------------------------------------------------------------------
# Time (for scheduled trades)
# ---------------------------------------------------------------------------

_TIME_RE: Final = re.compile(r"^\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*$")


def normalise_fire_at(
    raw: str,
    *,
    now: datetime,
    tz_offset_minutes: int,
) -> datetime | None:
    """Resolve a ``HH:MM`` (or ``HH:MM:SS``) string into an absolute UTC datetime.

    The supplied time is interpreted in the channel's timezone (encoded
    as a UTC offset so we don't drag pytz / zoneinfo tables around the
    hot path). If the resulting time has already passed today we
    advance to tomorrow.
    """
    s = raw.strip().lower()
    if s in {"now", "asap", "live", ""}:
        return None

    match = _TIME_RE.match(s)
    if match is None:
        return None

    h = int(match.group("h"))
    m = int(match.group("m"))
    sec = int(match.group("s") or 0)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        return None

    offset = timedelta(minutes=tz_offset_minutes)
    # "now" expressed as a naive local clock time.
    local_now = now.astimezone(UTC).replace(tzinfo=None) + offset
    target_local = datetime.combine(local_now.date(), time(h, m, sec))
    if target_local <= local_now:
        # Already passed today — schedule for tomorrow same time.
        target_local += timedelta(days=1)
    return (target_local - offset).replace(tzinfo=UTC)
