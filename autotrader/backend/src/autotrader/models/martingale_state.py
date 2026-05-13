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
    # Winning-streak (Paroli) state. ``current_win_streak`` ticks up
    # on each winning settle; ``last_payout`` records ``stake +
    # profit`` of that winning trade so the next channel signal can
    # size at ``ceil(last_payout)``. Both reset to 0 on a loss or
    # when ``current_win_streak >= winning_streak_max_level`` after
    # a win. At runtime the two ladders are mutually exclusive
    # (loss resets win_streak; win resets current_streak with
    # reset_on_win), so ``current_streak > 0 ⇔ current_win_streak == 0``.
    current_win_streak: int = Field(default=0, nullable=False)
    last_payout: float = Field(default=0.0, nullable=False)
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
    last_profit: float,
    max_streak: int,
    reset_on_win: bool,
    winning_streak_enabled: bool,
    winning_streak_max_level: int,
) -> MartingaleState:
    """Tick the loss-recovery + winning-streak counters after a settle.

    Args:
        won: True for win, False for loss.
        last_stake: stake the executor placed on the trade we just settled.
        last_profit: broker-reported profit (positive on win, negative
            on loss). Used to compute ``last_payout = last_stake +
            last_profit`` so the next streak step's stake is honest
            about real broker payout rather than a flat multiplier.
        max_streak: martingale recovery cap. Same semantic as before.
        reset_on_win: when True, a win clears current_streak.
        winning_streak_enabled: when True, advance current_win_streak
            on win + record last_payout. When False, the win-side
            counters stay at 0.
        winning_streak_max_level: cap for current_win_streak. After
            a win that hits the cap, reset to 0 and clear last_payout
            so the next channel signal returns to base. ``0`` means
            uncapped (rare).

    Implementation note: the martingale reset condition is
    ``current_streak > max_streak`` (strictly greater) — this is
    deliberate; see the existing comment block.
    """
    row = await get_state(session, parser_config_id)
    row.last_stake = last_stake
    row.last_outcome = "won" if won else "lost"
    if won:
        # Martingale: existing reset-on-win path.
        if reset_on_win:
            row.current_streak = 0
        # Winning-streak: advance + record payout, with max-level reset.
        if winning_streak_enabled:
            row.current_win_streak += 1
            # Phase 4 (audit 2026-05-13, M5): round to broker-precision
            # before persisting. ``last_payout`` carries forward into
            # the next streak step's stake calculation (``ceil(payout)``
            # in the risk gate), so float drift here can produce stake
            # ladders that are 1¢ off across long winning streaks.
            # The eventual money-column migration to Decimal is filed
            # as a Phase 5 follow-up; this is the bounded fix for now.
            row.last_payout = round(last_stake + last_profit, 2)
            if (
                winning_streak_max_level > 0
                and row.current_win_streak >= winning_streak_max_level
            ):
                row.current_win_streak = 0
                row.last_payout = 0.0
    else:
        # Loss: martingale advances; winning-streak resets unconditionally.
        row.current_streak += 1
        if max_streak > 0 and row.current_streak > max_streak:
            row.current_streak = 0
        row.current_win_streak = 0
        row.last_payout = 0.0
    row.updated_at = utc_now()
    await session.commit()
    await session.refresh(row)
    return row


async def reset_state(session: AsyncSession, parser_config_id: int) -> None:
    """Manual reset (for the dashboard). Clears BOTH ladders so the
    operator's "Reset" button is a single clean rewind."""
    row = await session.get(MartingaleState, parser_config_id)
    if row is None:
        return
    row.current_streak = 0
    row.current_win_streak = 0
    row.last_stake = 0.0
    row.last_payout = 0.0
    row.last_outcome = ""
    row.updated_at = utc_now()
    await session.commit()
