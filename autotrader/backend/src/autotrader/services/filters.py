"""Filter resolution for stats/v2 endpoints.

The three /stats/v2/* endpoints share the same query-param filter
bag. This module turns those raw query strings into validated,
type-safe filter objects + a where-clause builder usable by SQLModel
``select()``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

RangeLabel = Literal["24h", "7d", "30d", "all", "custom"]
Direction = Literal["call", "put"]
TradeStatus = Literal[
    "won", "lost", "rejected", "broker_error", "expired", "pending",
]


def resolve_range(
    label: str,
    *,
    now: datetime,
    custom_from: datetime | None = None,
    custom_to: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Map a range preset label to a (since, until) UTC pair.

    Unknown labels silently fall back to 24h so a stale URL doesn't
    blow up. ``custom`` requires both bounds; missing bounds also
    fall back to 24h to keep the behaviour predictable.

    All datetime arguments must be timezone-aware (UTC). Mixing
    aware and naive datetimes will raise TypeError downstream when
    the resulting (since, until) pair is compared in SQL queries.
    For ``custom`` with ``from > to`` we silently pass through;
    the resulting query returns zero rows but is well-formed.
    """
    if label == "all":
        return datetime(1970, 1, 1, tzinfo=UTC), now
    if label == "custom":
        if custom_from is None or custom_to is None:
            return now - timedelta(hours=24), now
        return custom_from, custom_to
    presets: dict[str, timedelta] = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = presets.get(label, timedelta(hours=24))
    return now - delta, now


class ResolvedFilter(BaseModel):
    """Validated filter values + computed time window.

    Endpoints receive this from ``resolve_filters_from_query`` (defined
    in stats_v2.py) and use it both to build SQL ``where`` clauses and
    to echo back to the caller in the ``filters_applied`` field of
    each response.
    """

    since: datetime
    until: datetime
    channels: list[int] = Field(default_factory=list)
    parsers: list[int] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    direction: Direction | None = None
    statuses: list[TradeStatus] = Field(default_factory=list)


def parse_csv_int(raw: str | None) -> list[int]:
    """Parse a comma-separated string of ints. Empty / None -> []."""
    if not raw:
        return []
    out: list[int] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            # Defensive: silently drop garbage rather than 400.
            # Stale URLs and typos shouldn't break the dashboard.
            continue
    return out


def parse_csv_str(raw: str | None) -> list[str]:
    """Parse a comma-separated string. Empty / None -> []."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def compute_parser_streaks(outcomes: list[str]) -> dict[str, object]:
    """Aggregate closed losing streaks for one parser's chronological run.

    A *closed* losing streak is a maximal run of consecutive ``lost``
    outcomes followed by at least one non-``lost`` outcome. The trailing
    run with no recovery yet is ignored — its length is not yet final.

    ``recovered_count`` counts streaks whose closing outcome is exactly
    ``won``; expired/rejected/broker_error close a streak (it's
    "complete") but do not count as recovery — only an actual win
    counts as bouncing back.

    Returns a dict with stringified-int histogram keys so JSON survives
    without numeric-key gymnastics on the wire.
    """
    longest = 0
    histogram: dict[str, int] = {}
    closed_total = 0
    recovered = 0

    run = 0
    for outcome in outcomes:
        if outcome == "lost":
            run += 1
            continue
        if run > 0:
            histogram[str(run)] = histogram.get(str(run), 0) + 1
            longest = max(longest, run)
            closed_total += 1
            if outcome == "won":
                recovered += 1
            run = 0

    rate = recovered / closed_total if closed_total else 0.0
    return {
        "longest_loss": longest,
        "histogram": histogram,
        "recovered_count": recovered,
        "recovery_rate": rate,
    }
