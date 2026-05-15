"""Persistent record of every executed trade attempt.

One row per attempt — covers both live (immediate) and scheduled
(pending) trades. Result fields (``status``, ``profit``, ``settled_at``)
are filled in asynchronously when the broker reports an outcome.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.base import utc_now

TradeStatus = Literal[
    "pending",       # placed, awaiting result
    "won",
    "lost",
    "rejected",      # risk gate blocked it before placement
    "broker_error",  # placement itself failed (invalid asset, closed market, …)
    "expired",       # scheduled but never resolved before timeout
]


class TradeAttempt(SQLModel, table=True):
    __tablename__ = "trade_attempts"

    id: int | None = Field(default=None, primary_key=True)

    chat_id: int = Field(index=True, nullable=False)
    parser_config_id: int = Field(index=True, nullable=False)

    # Signal projection (denormalised for cheap history queries).
    asset: str = Field(nullable=False)
    asset_raw: str = Field(default="", nullable=False)
    direction: str = Field(nullable=False)         # "call" | "put"
    duration_seconds: int = Field(nullable=False)
    stake: float = Field(nullable=False)
    trade_mode: str = Field(nullable=False)         # "live" | "scheduled"
    fire_at: datetime | None = Field(default=None, index=True)

    # Phase 2 idempotency key (audit 2026-05-13, H1). The Telegram
    # message that produced this attempt — composite-unique with
    # ``chat_id`` (Telegram numbers messages per-chat, so the same
    # ``id`` in two chats is legitimate). ``None`` when the source
    # didn't carry an id (manual replay via the parser test endpoint,
    # legacy rows from before this column existed). Pipeline dispatch
    # short-circuits when it finds an existing row with the same
    # ``(chat_id, tg_message_id)`` within the dedup window — that's
    # the H1 fix.
    tg_message_id: int | None = Field(default=None, index=True)

    # Outcome.
    status: str = Field(default="pending", index=True, nullable=False)
    broker_order_id: str | None = Field(default=None, index=True)
    profit: float | None = Field(default=None)
    error: str | None = Field(default=None)
    raw_text: str = Field(default="", nullable=False)

    # Lifecycle timestamps.
    received_at: datetime = Field(default_factory=utc_now, nullable=False)
    placed_at: datetime | None = Field(default=None)
    settled_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


async def list_recent(
    session: AsyncSession,
    *,
    limit: int = 50,
    chat_id: int | None = None,
) -> list[TradeAttempt]:
    stmt = select(TradeAttempt).order_by(TradeAttempt.created_at.desc())  # type: ignore[attr-defined]
    if chat_id is not None:
        stmt = stmt.where(TradeAttempt.chat_id == chat_id)
    stmt = stmt.limit(limit)
    result = await session.exec(stmt)
    return list(result.all())


async def list_pending(session: AsyncSession) -> list[TradeAttempt]:
    """Every row still in ``pending`` state — used by the startup reconciler."""
    stmt = select(TradeAttempt).where(TradeAttempt.status == "pending")
    return list((await session.exec(stmt)).all())


async def find_recent_by_tg_message_id(
    session: AsyncSession,
    *,
    chat_id: int,
    tg_message_id: int,
    lookback: timedelta = timedelta(minutes=10),
) -> TradeAttempt | None:
    """Return the most recent attempt whose ``(chat_id, tg_message_id)``
    matches, created within ``lookback``. ``None`` if no match.

    Phase 2 (audit 2026-05-13, H1): the pipeline calls this *before*
    building parsers so a Pyrogram replay short-circuits without
    producing a duplicate trade. The 10-minute window is generous —
    Pyrogram's catch-up window is much shorter — but cheap to widen
    because the index on ``tg_message_id`` makes the query effectively
    constant-time.
    """
    cutoff = utc_now() - lookback
    stmt = (
        select(TradeAttempt)
        .where(TradeAttempt.chat_id == chat_id)
        .where(TradeAttempt.tg_message_id == tg_message_id)
        .where(TradeAttempt.created_at >= cutoff)
        .order_by(TradeAttempt.created_at.desc())  # type: ignore[attr-defined]
        .limit(1)
    )
    rows = list((await session.exec(stmt)).all())
    return rows[0] if rows else None


async def insert_attempt(
    session: AsyncSession,
    attempt: TradeAttempt,
) -> TradeAttempt:
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt


async def update_attempt(
    session: AsyncSession,
    attempt_id: int,
    *,
    status: str | None = None,
    broker_order_id: str | None = None,
    profit: float | None = None,
    error: str | None = None,
    placed_at: datetime | None = None,
    settled_at: datetime | None = None,
) -> TradeAttempt | None:
    row = await session.get(TradeAttempt, attempt_id)
    if row is None:
        return None
    if status is not None:
        row.status = status
    if broker_order_id is not None:
        row.broker_order_id = broker_order_id
    if profit is not None:
        row.profit = profit
    if error is not None:
        row.error = error
    if placed_at is not None:
        row.placed_at = placed_at
    if settled_at is not None:
        row.settled_at = settled_at
    row.updated_at = utc_now()
    await session.commit()
    await session.refresh(row)
    return row
