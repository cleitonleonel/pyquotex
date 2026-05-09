"""Per-parser martingale runtime state.

Phase 3 persists the *config* (multiplier, max_streak, reset_on_win)
on the parser config row; Phase 5 owns the *runtime* — the current
losing-streak counter and the last stake we placed. One row per
``parser_config_id`` so each parser keeps its own ladder.

The executor's result watcher updates this row on every settled
trade: reset on win, increment on loss (capped at ``max_streak``).
The risk gate reads it before placement to compute the next stake.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.base import utc_now


class MartingaleState(SQLModel, table=True):
    """One row per parser config; ``parser_config_id`` is the PK."""

    __tablename__ = "martingale_states"

    parser_config_id: int = Field(primary_key=True)
    current_streak: int = Field(default=0, nullable=False)
    last_stake: float = Field(default=0.0, nullable=False)
    last_outcome: str = Field(default="", nullable=False)  # "" | "won" | "lost"
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


async def get_state(session: AsyncSession, parser_config_id: int) -> MartingaleState:
    """Return the live state, creating it on first read."""
    row = await session.get(MartingaleState, parser_config_id)
    if row is not None:
        return row
    row = MartingaleState(parser_config_id=parser_config_id)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def record_outcome(
    session: AsyncSession,
    parser_config_id: int,
    *,
    won: bool,
    last_stake: float,
    max_streak: int,
    reset_on_win: bool,
) -> MartingaleState:
    """Tick the streak counter after a settled trade.

    Args:
        won: True for win, False for loss.
        last_stake: the stake the executor actually placed.
        max_streak: number of recovery steps the ladder is allowed to
            climb before resetting to base. ``max_streak=1`` mirrors a
            channel's *"TAKE 1 STEP MTG"* directive — one recovery
            trade after a loss, then bail. ``max_streak=2`` allows two
            recoveries (steps 1 and 2). ``0`` means uncapped.
        reset_on_win: when True, a win clears the streak (the usual
            behaviour); when False, the streak keeps climbing across
            wins (rare, but channels exist).

    Implementation note: the reset condition is ``current_streak >
    max_streak`` (strictly greater), not ``>=``. The streak is
    incremented *before* the check, so if we used ``>=`` the very
    first loss would push streak from 0→1 and then reset it to 0 on
    the same call — making ``max_streak=1`` a no-op (zero recoveries).
    With ``>`` the streak survives long enough to be read by the
    *next* trade's stake calculation, then resets only when a further
    loss pushes it past the cap.
    """
    row = await get_state(session, parser_config_id)
    row.last_stake = last_stake
    row.last_outcome = "won" if won else "lost"
    if won:
        if reset_on_win:
            row.current_streak = 0
    else:
        row.current_streak += 1
        if max_streak > 0 and row.current_streak > max_streak:
            row.current_streak = 0
    row.updated_at = utc_now()
    await session.commit()
    await session.refresh(row)
    return row


async def reset_state(session: AsyncSession, parser_config_id: int) -> None:
    """Manual reset (for the dashboard)."""
    row = await session.get(MartingaleState, parser_config_id)
    if row is None:
        return
    row.current_streak = 0
    row.last_stake = 0.0
    row.last_outcome = ""
    row.updated_at = utc_now()
    await session.commit()
