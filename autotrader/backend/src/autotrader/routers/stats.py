"""Dashboard stats — per-channel aggregates + latency percentiles.

Phase 6 surfaces. Both the channel breakdown and the latency tiles
operate on today's ``trade_attempts`` rows (UTC) so a long-running
deployment doesn't accumulate into a single average that hides
recent regressions. The window is the same one the risk-gate's
budget endpoint uses, keeping all "today" numbers consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.auth import require_auth
from autotrader.dependencies import SessionDep
from autotrader.models.parser_config import ParserConfig
from autotrader.models.trade_attempt import TradeAttempt
from autotrader.models.watched_channel import WatchedChannel

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(require_auth)])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChannelStats(BaseModel):
    chat_id: int
    title: str
    total: int
    won: int
    lost: int
    rejected: int
    broker_error: int
    expired: int
    pending: int
    win_rate: float | None  # null when there are no settled trades
    realised_pnl: float
    committed_stake: float


class LatencyTile(BaseModel):
    """Percentiles in milliseconds, plus the sample count."""

    count: int
    p50_ms: float | None
    p99_ms: float | None


class LatencyStats(BaseModel):
    signal_to_place: LatencyTile
    place_to_settle: LatencyTile


class StatsOverview(BaseModel):
    channels: list[ChannelStats]
    latency: LatencyStats
    window: Literal["today_utc"] = "today_utc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_start() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time(0, 0), tzinfo=UTC)


def _percentile(samples: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. ``samples`` need not be sorted.

    Returns ``None`` for empty input. Defined locally so we don't pull
    in numpy for two numbers per request.
    """
    if not samples:
        return None
    ordered = sorted(samples)
    # ``pct`` is 0-100; clamp so callers can't trip an IndexError.
    rank = max(0.0, min(100.0, pct)) / 100.0 * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


async def _channel_stats(
    session: AsyncSession,
    *,
    since: datetime,
) -> list[ChannelStats]:
    watched_rows = (await session.exec(select(WatchedChannel))).all()
    titles = {row.chat_id: row.title for row in watched_rows}
    enabled_watched = {row.chat_id for row in watched_rows if row.enabled}

    # Channels with at least one enabled parser deserve a row even when
    # nothing has fired today — otherwise an idle channel looks
    # un-configured on the dashboard.
    parser_chat_ids = (
        await session.exec(
            select(ParserConfig.chat_id).where(ParserConfig.enabled.is_(True)),  # type: ignore[attr-defined]
        )
    ).all()
    armed_chats = enabled_watched & set(parser_chat_ids)

    # Group all of today's attempts by chat_id and bucket the counts in
    # one pass. SQLAlchemy ``case`` is overkill here — the row volume
    # is small (~hundreds per day) and the post-filter Python loop is
    # easier to reason about than a pile of conditional aggregates.
    rows = (
        await session.exec(
            select(TradeAttempt).where(TradeAttempt.received_at >= since),
        )
    ).all()

    by_chat: dict[int, list[TradeAttempt]] = {chat_id: [] for chat_id in armed_chats}
    for row in rows:
        by_chat.setdefault(row.chat_id, []).append(row)

    out: list[ChannelStats] = []
    for chat_id, attempts in by_chat.items():
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
        out.append(
            ChannelStats(
                chat_id=chat_id,
                title=titles.get(chat_id, f"chat {chat_id}"),
                total=len(attempts),
                won=won,
                lost=lost,
                rejected=rejected,
                broker_error=broker_error,
                expired=expired,
                pending=pending,
                win_rate=(won / settled) if settled else None,
                realised_pnl=pnl,
                committed_stake=committed,
            ),
        )
    # Most active first; ties broken by win rate so good channels
    # stand out.
    out.sort(key=lambda c: (c.total, c.win_rate or 0.0), reverse=True)
    return out


async def _latency_stats(
    session: AsyncSession,
    *,
    since: datetime,
) -> LatencyStats:
    rows = (
        await session.exec(
            select(
                TradeAttempt.received_at,
                TradeAttempt.placed_at,
                TradeAttempt.settled_at,
            ).where(TradeAttempt.received_at >= since),
        )
    ).all()

    sig_to_place_ms: list[float] = []
    place_to_settle_ms: list[float] = []
    for received_at, placed_at, settled_at in rows:
        if placed_at is not None and received_at is not None:
            sig_to_place_ms.append(
                (placed_at - received_at).total_seconds() * 1000.0,
            )
        if settled_at is not None and placed_at is not None:
            place_to_settle_ms.append(
                (settled_at - placed_at).total_seconds() * 1000.0,
            )

    return LatencyStats(
        signal_to_place=LatencyTile(
            count=len(sig_to_place_ms),
            p50_ms=_percentile(sig_to_place_ms, 50),
            p99_ms=_percentile(sig_to_place_ms, 99),
        ),
        place_to_settle=LatencyTile(
            count=len(place_to_settle_ms),
            p50_ms=_percentile(place_to_settle_ms, 50),
            p99_ms=_percentile(place_to_settle_ms, 99),
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=StatsOverview)
async def overview_endpoint(session: SessionDep) -> StatsOverview:
    since = _today_start()
    channels = await _channel_stats(session, since=since)
    latency = await _latency_stats(session, since=since)
    return StatsOverview(channels=channels, latency=latency)
