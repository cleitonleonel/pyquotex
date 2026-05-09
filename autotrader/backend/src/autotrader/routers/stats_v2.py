"""Stats v2 endpoints — timeseries / breakdown / funnel.

These ship in Phase 2 of the UI modernization. They share the
``_resolve_filters_from_query`` helper for the common filter bag and
the ``_resolve_bucket`` helper for time-series binning.

The legacy ``/stats/overview`` stays in place until Phase 3 cleanup
removes its callers; the new endpoints are additive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, get_args

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import select

from autotrader.auth import require_auth
from autotrader.dependencies import PipelineDep, SessionDep
from autotrader.models.parser_config import ParserConfig
from autotrader.models.trade_attempt import TradeAttempt
from autotrader.models.watched_channel import WatchedChannel
from autotrader.services.filters import (
    Direction,
    ResolvedFilter,
    TradeStatus,
    compute_parser_streaks,
    parse_csv_int,
    parse_csv_str,
    resolve_range,
)

router = APIRouter(
    prefix="/stats/v2",
    tags=["stats-v2"],
    dependencies=[Depends(require_auth)],
)

Metric = Literal[
    "equity", "win_rate", "latency_p50", "latency_p99", "outcomes",
]
Bucket = Literal["auto", "hour", "day", "week"]


class TimeseriesPoint(BaseModel):
    t: datetime
    value: float | None = None
    n: int = 0
    counts: dict[str, int] | None = None  # only set when metric=outcomes


class TimeseriesResponse(BaseModel):
    metric: Metric
    bucket: Bucket
    points: list[TimeseriesPoint]
    filters_applied: dict


Dim = Literal["channel", "parser", "asset", "direction", "hour_of_week"]


class BreakdownRow(BaseModel):
    key: int | str
    label: str
    total: int
    won: int
    lost: int
    rejected: int
    broker_error: int
    expired: int
    pending: int
    win_rate: float | None
    win_rate_ci_low: float | None = None
    win_rate_ci_high: float | None = None
    realised_pnl: float
    committed_stake: float
    # Per-dim extras attached as needed (DirectionSplit for asset,
    # streaks for parser).
    direction_split: DirectionSplit | None = None
    # Streaks roll-up for dim=parser rows: a dict with keys
    # ``longest_loss`` (int), ``histogram`` (dict[str, int] of
    # closed-streak length -> count), ``recovered_count`` (int) and
    # ``recovery_rate`` (float, 0 when no closed streaks). None for
    # all other dims.
    streaks: dict | None = None


class DirectionSplit(BaseModel):
    """Per-direction sub-stats attached to BreakdownRow when dim=asset.

    Both fields are full BreakdownRow shapes computed from the
    asset's call vs put trades respectively.
    """

    call: BreakdownRow
    put: BreakdownRow


# Resolve the forward reference now that DirectionSplit is defined.
BreakdownRow.model_rebuild()


class BreakdownResponse(BaseModel):
    dim: Dim
    rows: list[BreakdownRow]
    filters_applied: dict


class FunnelStage(BaseModel):
    key: Literal[
        "messages_received", "matched", "passed_risk",
        "placed", "settled",
    ]
    label: str
    count: int


class FunnelDropReason(BaseModel):
    reason: str
    count: int


class FunnelResponse(BaseModel):
    stages: list[FunnelStage]
    drop_reasons: dict[str, list[FunnelDropReason]]
    filters_applied: dict
    # Discriminator for the messages_received / matched stage counts:
    # ``"ring"`` means "last N entries from the in-memory pipeline
    # decision ring" (bounded by Pipeline._DECISION_RING_SIZE), not
    # "last <range> of decisions". The frontend uses this to label
    # the stage truthfully — it should not imply the count scales
    # with the user's selected range.
    messages_received_window: Literal["ring"] = "ring"


def _resolve_bucket(
    bucket: Bucket,
    since: datetime,
    until: datetime,
) -> timedelta:
    """auto: <=24h -> 15min, <=7d -> hour, <=30d -> day, all -> day."""
    if bucket == "hour":
        return timedelta(hours=1)
    if bucket == "day":
        return timedelta(days=1)
    if bucket == "week":
        return timedelta(days=7)
    span = until - since
    if span <= timedelta(hours=24):
        return timedelta(minutes=15)
    if span <= timedelta(days=7):
        return timedelta(hours=1)
    return timedelta(days=1)


def _as_utc(ts: datetime) -> datetime:
    """Return ``ts`` as a UTC-aware datetime.

    SQLite + aiosqlite returns stored datetimes as naive (no tzinfo).
    The model writes them via ``utc_now()`` which is UTC-aware, so
    we can safely treat naive datetimes from the DB as UTC.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _floor_to_bucket(
    ts: datetime,
    bucket_size: timedelta,
    anchor: datetime,
) -> datetime:
    """Round ``ts`` down to its bucket's start, anchored to ``anchor``."""
    delta = _as_utc(ts) - anchor
    n = int(delta.total_seconds() // bucket_size.total_seconds())
    return anchor + bucket_size * n


def _wilson_ci(
    successes: int, n: int, *, z: float = 1.96,
) -> tuple[float | None, float | None]:
    """Wilson score interval at the given z (default 95% = 1.96).

    Returns (low, high). For n=0 returns (None, None) — there's no
    interval to draw on the chart.
    """
    if n == 0:
        return None, None
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _percentile(samples: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. Returns None for empty input.

    Same shape as the helper in routers/stats.py; defined locally
    here to keep the v2 router free of imports from the legacy one.
    """
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(0.0, min(100.0, pct)) / 100.0 * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _build_breakdown_row(
    *, key: int | str, label: str, attempts: list[TradeAttempt],
) -> BreakdownRow:
    """Compute the standard per-row fields shared across all dims."""
    won = sum(1 for r in attempts if r.status == "won")
    lost = sum(1 for r in attempts if r.status == "lost")
    rejected = sum(1 for r in attempts if r.status == "rejected")
    broker_error = sum(1 for r in attempts if r.status == "broker_error")
    expired = sum(1 for r in attempts if r.status == "expired")
    pending = sum(1 for r in attempts if r.status == "pending")
    settled = won + lost
    pnl = sum(r.profit or 0.0 for r in attempts)
    committed = sum(
        r.stake for r in attempts if r.status in ("pending", "won", "lost")
    )
    win_rate = (won / settled) if settled else None
    ci_low, ci_high = _wilson_ci(won, settled) if settled else (None, None)
    return BreakdownRow(
        key=key,
        label=label,
        total=len(attempts),
        won=won,
        lost=lost,
        rejected=rejected,
        broker_error=broker_error,
        expired=expired,
        pending=pending,
        win_rate=win_rate,
        win_rate_ci_low=ci_low,
        win_rate_ci_high=ci_high,
        realised_pnl=pnl,
        committed_stake=committed,
    )


async def _resolve_filters_from_query(
    range_label: str,
    custom_from: datetime | None,
    custom_to: datetime | None,
    channels_csv: str | None,
    parsers_csv: str | None,
    assets_csv: str | None,
    direction: str | None,
    statuses_csv: str | None,
) -> ResolvedFilter:
    # Pydantic parses ISO datetimes without offset as naive — normalize
    # to UTC so downstream arithmetic with f.since/f.until (always UTC)
    # doesn't TypeError.
    if custom_from is not None and custom_from.tzinfo is None:
        custom_from = custom_from.replace(tzinfo=UTC)
    if custom_to is not None and custom_to.tzinfo is None:
        custom_to = custom_to.replace(tzinfo=UTC)
    since, until = resolve_range(
        range_label,
        now=datetime.now(UTC),
        custom_from=custom_from,
        custom_to=custom_to,
    )
    valid_directions = set(get_args(Direction))
    valid_statuses = set(get_args(TradeStatus))
    return ResolvedFilter(
        since=since,
        until=until,
        channels=parse_csv_int(channels_csv),
        parsers=parse_csv_int(parsers_csv),
        assets=parse_csv_str(assets_csv),
        direction=direction if direction in valid_directions else None,  # type: ignore[arg-type]
        statuses=[
            s for s in parse_csv_str(statuses_csv) if s in valid_statuses
        ],  # type: ignore[misc]
    )


def _apply_filters(stmt, f: ResolvedFilter):
    """Apply a ResolvedFilter to a select() statement on TradeAttempt."""
    stmt = stmt.where(TradeAttempt.received_at >= f.since)
    stmt = stmt.where(TradeAttempt.received_at < f.until)
    if f.channels:
        stmt = stmt.where(TradeAttempt.chat_id.in_(f.channels))  # type: ignore[attr-defined]
    if f.parsers:
        stmt = stmt.where(TradeAttempt.parser_config_id.in_(f.parsers))  # type: ignore[attr-defined]
    if f.assets:
        stmt = stmt.where(TradeAttempt.asset.in_(f.assets))  # type: ignore[attr-defined]
    if f.direction:
        stmt = stmt.where(TradeAttempt.direction == f.direction)
    if f.statuses:
        stmt = stmt.where(TradeAttempt.status.in_(f.statuses))  # type: ignore[attr-defined]
    return stmt


@router.get("/timeseries", response_model=TimeseriesResponse)
async def timeseries_endpoint(  # noqa: PLR0912, PLR0915
    session: SessionDep,
    metric: Metric = Query(...),  # noqa: B008
    range: str = Query("24h", alias="range"),
    bucket: Bucket = Query("auto"),  # noqa: B008
    custom_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    custom_to: datetime | None = Query(None, alias="to"),  # noqa: B008
    channels: str | None = Query(None),
    parsers: str | None = Query(None),
    assets: str | None = Query(None),
    direction: str | None = Query(None),
    statuses: str | None = Query(None),
) -> TimeseriesResponse:
    f = await _resolve_filters_from_query(
        range, custom_from, custom_to, channels, parsers, assets,
        direction, statuses,
    )
    bucket_size = _resolve_bucket(bucket, f.since, f.until)
    rows = (await session.exec(_apply_filters(select(TradeAttempt), f))).all()

    if metric == "equity":
        per_bucket: dict[datetime, float] = {}
        per_bucket_count: dict[datetime, int] = {}
        for row in rows:
            if row.profit is None:
                continue
            bucket_ts = _floor_to_bucket(
                row.received_at, bucket_size, f.since,
            )
            per_bucket[bucket_ts] = per_bucket.get(bucket_ts, 0.0) + row.profit
            per_bucket_count[bucket_ts] = per_bucket_count.get(bucket_ts, 0) + 1
        ordered = sorted(per_bucket.keys())
        cumulative = 0.0
        points: list[TimeseriesPoint] = []
        for ts in ordered:
            cumulative += per_bucket[ts]
            points.append(
                TimeseriesPoint(t=ts, value=cumulative, n=per_bucket_count[ts]),
            )
        return TimeseriesResponse(
            metric="equity",
            bucket=bucket,
            points=points,
            filters_applied=f.model_dump(mode="json"),
        )

    if metric == "win_rate":
        # Per-bucket: won_count / settled_count. Bucket emits no point
        # when settled_count is zero so the chart doesn't draw a flat
        # line through "no data" buckets.
        won_per_bucket: dict[datetime, int] = {}
        settled_per_bucket: dict[datetime, int] = {}
        for row in rows:
            if row.status not in ("won", "lost"):
                continue
            bucket_ts = _floor_to_bucket(
                row.received_at, bucket_size, f.since,
            )
            settled_per_bucket[bucket_ts] = settled_per_bucket.get(bucket_ts, 0) + 1
            if row.status == "won":
                won_per_bucket[bucket_ts] = won_per_bucket.get(bucket_ts, 0) + 1
        ordered_wr = sorted(settled_per_bucket.keys())
        points_wr: list[TimeseriesPoint] = []
        for ts in ordered_wr:
            n = settled_per_bucket[ts]
            won = won_per_bucket.get(ts, 0)
            points_wr.append(
                TimeseriesPoint(t=ts, value=won / n if n else None, n=n),
            )
        return TimeseriesResponse(
            metric="win_rate",
            bucket=bucket,
            points=points_wr,
            filters_applied=f.model_dump(mode="json"),
        )

    if metric in ("latency_p50", "latency_p99"):
        pct = 50.0 if metric == "latency_p50" else 99.0
        # Group (placed - received) ms samples per bucket, then take
        # the percentile of each bucket's samples.
        per_bucket_samples: dict[datetime, list[float]] = {}
        for row in rows:
            if row.placed_at is None or row.received_at is None:
                continue
            bucket_ts = _floor_to_bucket(
                row.received_at, bucket_size, f.since,
            )
            ms = (row.placed_at - row.received_at).total_seconds() * 1000.0
            per_bucket_samples.setdefault(bucket_ts, []).append(ms)
        ordered_lat = sorted(per_bucket_samples.keys())
        points_lat: list[TimeseriesPoint] = []
        for ts in ordered_lat:
            samples = per_bucket_samples[ts]
            points_lat.append(
                TimeseriesPoint(
                    t=ts,
                    value=_percentile(samples, pct),
                    n=len(samples),
                ),
            )
        return TimeseriesResponse(
            metric=metric,
            bucket=bucket,
            points=points_lat,
            filters_applied=f.model_dump(mode="json"),
        )

    if metric == "outcomes":
        # Per-bucket counts of each status. Returned as a `counts`
        # dict instead of `value` so the streak-distribution stacked
        # bar can read each status independently in one round-trip.
        per_bucket_counts: dict[datetime, dict[str, int]] = {}
        zero_counts = {
            "won": 0, "lost": 0, "rejected": 0,
            "broker_error": 0, "expired": 0, "pending": 0,
        }
        for row in rows:
            if row.status not in zero_counts:
                continue
            bucket_ts = _floor_to_bucket(
                row.received_at, bucket_size, f.since,
            )
            status_counts = per_bucket_counts.setdefault(bucket_ts, dict(zero_counts))
            status_counts[row.status] += 1
        ordered_out = sorted(per_bucket_counts.keys())
        points_out: list[TimeseriesPoint] = []
        for ts in ordered_out:
            status_counts = per_bucket_counts[ts]
            points_out.append(
                TimeseriesPoint(
                    t=ts,
                    value=None,
                    n=sum(status_counts.values()),
                    counts=status_counts,
                ),
            )
        return TimeseriesResponse(
            metric="outcomes",
            bucket=bucket,
            points=points_out,
            filters_applied=f.model_dump(mode="json"),
        )

    # Unreachable: FastAPI validates metric against the Literal type.
    # Included as a safety net for type-checkers.
    return TimeseriesResponse(
        metric=metric,
        bucket=bucket,
        points=[],
        filters_applied=f.model_dump(mode="json"),
    )


@router.get("/breakdown", response_model=BreakdownResponse)
async def breakdown_endpoint(  # noqa: PLR0912, PLR0915
    session: SessionDep,
    dim: Dim = Query(...),  # noqa: B008
    range: str = Query("24h", alias="range"),
    custom_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    custom_to: datetime | None = Query(None, alias="to"),  # noqa: B008
    channels: str | None = Query(None),
    parsers: str | None = Query(None),
    assets: str | None = Query(None),
    direction: str | None = Query(None),
    statuses: str | None = Query(None),
) -> BreakdownResponse:
    f = await _resolve_filters_from_query(
        range, custom_from, custom_to, channels, parsers, assets,
        direction, statuses,
    )
    rows = (await session.exec(_apply_filters(select(TradeAttempt), f))).all()

    if dim == "channel":
        # Resolve labels from WatchedChannel rows. Trades from chats
        # without a WatchedChannel row still appear, labelled `chat N`.
        watched = (await session.exec(select(WatchedChannel))).all()
        titles = {w.chat_id: w.title for w in watched}
        grouped_c: dict[int, list[TradeAttempt]] = {}
        for row in rows:
            grouped_c.setdefault(row.chat_id, []).append(row)
        out_c: list[BreakdownRow] = []
        for chat_id, attempts in grouped_c.items():
            out_c.append(_build_breakdown_row(
                key=chat_id,
                label=titles.get(chat_id, f"chat {chat_id}"),
                attempts=attempts,
            ))
        out_c.sort(key=lambda r: (r.total, r.win_rate or 0.0), reverse=True)
        return BreakdownResponse(
            dim="channel",
            rows=out_c,
            filters_applied=f.model_dump(mode="json"),
        )

    if dim == "parser":
        configs = (await session.exec(select(ParserConfig))).all()
        names = {c.id: c.name for c in configs if c.id is not None}
        grouped_p: dict[int, list[TradeAttempt]] = {}
        for row in rows:
            grouped_p.setdefault(row.parser_config_id, []).append(row)
        out_p: list[BreakdownRow] = []
        for parser_id, attempts in grouped_p.items():
            base = _build_breakdown_row(
                key=parser_id,
                label=names.get(parser_id, f"parser {parser_id}"),
                attempts=attempts,
            )
            # Streaks need chronological order — the SQL query has no
            # ORDER BY so attempts arrive in insertion order; sort
            # explicitly so streak boundaries match wall-clock reality
            # rather than DB-page layout.
            ordered = sorted(attempts, key=lambda a: a.received_at)
            base.streaks = compute_parser_streaks(
                [a.status for a in ordered],
            )
            out_p.append(base)
        out_p.sort(key=lambda r: (r.total, r.win_rate or 0.0), reverse=True)
        return BreakdownResponse(
            dim="parser",
            rows=out_p,
            filters_applied=f.model_dump(mode="json"),
        )

    if dim == "asset":
        # Asset rows include a direction_split sub-stats dict so the
        # asset*direction matrix panel can read both polarities in one
        # round-trip.
        grouped_a: dict[str, list[TradeAttempt]] = {}
        for row in rows:
            grouped_a.setdefault(row.asset, []).append(row)
        out_a: list[BreakdownRow] = []
        for asset_sym, attempts in grouped_a.items():
            base = _build_breakdown_row(
                key=asset_sym, label=asset_sym, attempts=attempts,
            )
            calls = [a for a in attempts if a.direction == "call"]
            puts = [a for a in attempts if a.direction == "put"]
            base.direction_split = DirectionSplit(
                call=_build_breakdown_row(
                    key=asset_sym, label=asset_sym, attempts=calls,
                ),
                put=_build_breakdown_row(
                    key=asset_sym, label=asset_sym, attempts=puts,
                ),
            )
            out_a.append(base)
        out_a.sort(key=lambda r: (r.total, r.win_rate or 0.0), reverse=True)
        return BreakdownResponse(
            dim="asset",
            rows=out_a,
            filters_applied=f.model_dump(mode="json"),
        )

    if dim == "direction":
        grouped_d: dict[str, list[TradeAttempt]] = {"call": [], "put": []}
        for row in rows:
            if row.direction in grouped_d:
                grouped_d[row.direction].append(row)
        out_d: list[BreakdownRow] = []
        for direction_key in ("call", "put"):
            attempts = grouped_d[direction_key]
            if not attempts:
                continue
            out_d.append(_build_breakdown_row(
                key=direction_key,
                label=direction_key.upper(),
                attempts=attempts,
            ))
        return BreakdownResponse(
            dim="direction",
            rows=out_d,
            filters_applied=f.model_dump(mode="json"),
        )

    if dim == "hour_of_week":
        # 168 cells (7 days * 24 hours). key = weekday * 24 + hour
        # where Monday = 0.
        grouped_h: dict[int, list[TradeAttempt]] = {}
        for row in rows:
            ts = _as_utc(row.received_at)
            key = ts.weekday() * 24 + ts.hour
            grouped_h.setdefault(key, []).append(row)
        out_h: list[BreakdownRow] = []
        weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for key, attempts in grouped_h.items():
            wd = key // 24
            hr = key % 24
            out_h.append(_build_breakdown_row(
                key=key,
                label=f"{weekday_names[wd]} {hr:02d}:00",
                attempts=attempts,
            ))
        out_h.sort(key=lambda r: r.key)  # type: ignore[arg-type, return-value]
        return BreakdownResponse(
            dim="hour_of_week",
            rows=out_h,
            filters_applied=f.model_dump(mode="json"),
        )

    # Unreachable: FastAPI validates dim against the Literal type.
    return BreakdownResponse(
        dim=dim,
        rows=[],
        filters_applied=f.model_dump(mode="json"),
    )


@router.get("/assets")
async def assets_endpoint(
    session: SessionDep,
    range: str = Query("24h", alias="range"),
    custom_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    custom_to: datetime | None = Query(None, alias="to"),  # noqa: B008
) -> dict[str, list[str]]:
    """Distinct asset symbols traded inside the time window.

    Powers the asset filter pill on the frontend. Deliberately does
    not accept chat/parser/direction filters: the pill must show the
    full universe of assets so an operator can pivot across channels
    without first clearing the other filter pills. Sorted case-
    insensitively for stable UI ordering.

    Pydantic parses ISO datetimes without offset as naive — normalize
    to UTC before passing to ``resolve_range`` so the comparison with
    ``received_at`` (always UTC) doesn't TypeError.
    """
    if custom_from is not None and custom_from.tzinfo is None:
        custom_from = custom_from.replace(tzinfo=UTC)
    if custom_to is not None and custom_to.tzinfo is None:
        custom_to = custom_to.replace(tzinfo=UTC)
    since, until = resolve_range(
        range,
        now=datetime.now(UTC),
        custom_from=custom_from,
        custom_to=custom_to,
    )
    stmt = (
        select(TradeAttempt.asset)
        .where(TradeAttempt.received_at >= since)
        .where(TradeAttempt.received_at < until)
        .distinct()
    )
    rows = (await session.exec(stmt)).all()
    return {"assets": sorted({r for r in rows if r}, key=str.lower)}


@router.get("/funnel", response_model=FunnelResponse)
async def funnel_endpoint(
    session: SessionDep,
    pipeline: PipelineDep,
    range: str = Query("24h", alias="range"),
    custom_from: datetime | None = Query(None, alias="from"),  # noqa: B008
    custom_to: datetime | None = Query(None, alias="to"),  # noqa: B008
    channels: str | None = Query(None),
    parsers: str | None = Query(None),
    assets: str | None = Query(None),
    direction: str | None = Query(None),
    statuses: str | None = Query(None),
) -> FunnelResponse:
    """Five-stage signal funnel: received -> matched -> passed risk
    -> placed -> settled.

    The downstream three stages (passed_risk / placed / settled) come
    from ``trade_attempts`` and honour the ``range`` window. The two
    upstream stages (messages_received / matched) come from the live
    ``Pipeline.recent_decisions`` ring — a bounded in-memory deque
    (``Pipeline._DECISION_RING_SIZE``, currently 200). Because the
    ring is bounded and lives in process memory, those counts are
    "last N decisions" not "last <range> of decisions"; the
    ``messages_received_window`` field on the response surfaces that
    distinction so the frontend can label the stage honestly.
    """
    f = await _resolve_filters_from_query(
        range, custom_from, custom_to, channels, parsers, assets,
        direction, statuses,
    )
    rows = (await session.exec(_apply_filters(select(TradeAttempt), f))).all()

    # trade-attempts-derived counts (downstream three stages)
    total = len(rows)
    rejected = sum(1 for row in rows if row.status == "rejected")
    broker_error = sum(1 for row in rows if row.status == "broker_error")
    settled_count = sum(1 for row in rows if row.status in ("won", "lost"))
    passed_risk = total - rejected
    placed = total - rejected - broker_error

    # decisions-ring-derived counts (upstream two stages). Snapshot
    # the ring once so the two derived counts agree on the same view —
    # the property returns a fresh list, but a concurrent dispatch
    # could otherwise grow it between the two reads.
    ring = pipeline.recent_decisions
    messages_received = len(ring)
    matched = sum(1 for d in ring if d.get("outcome") == "matched")

    stages = [
        FunnelStage(key="messages_received", label="Messages received",
                    count=messages_received),
        FunnelStage(key="matched", label="Parser matched", count=matched),
        FunnelStage(key="passed_risk", label="Passed risk gate",
                    count=passed_risk),
        FunnelStage(key="placed", label="Placed at broker", count=placed),
        FunnelStage(key="settled", label="Settled (won + lost)",
                    count=settled_count),
    ]

    # Drop reasons: parse rejected rows' `error` column for known
    # reasons. The risk gate writes consistent strings (e.g.
    # "daily_loss_cap_exhausted", "concurrency_cap",
    # "kill_switch_engaged") into `error` so a simple counter works.
    matched_to_passed_risk: dict[str, int] = {}
    for row in rows:
        if row.status == "rejected" and row.error:
            matched_to_passed_risk[row.error] = (
                matched_to_passed_risk.get(row.error, 0) + 1
            )
    drop_reasons = {
        "matched_to_passed_risk": [
            FunnelDropReason(reason=k, count=v)
            for k, v in sorted(
                matched_to_passed_risk.items(), key=lambda kv: -kv[1],
            )
        ],
    }

    return FunnelResponse(
        stages=stages,
        drop_reasons=drop_reasons,
        filters_applied=f.model_dump(mode="json"),
    )
