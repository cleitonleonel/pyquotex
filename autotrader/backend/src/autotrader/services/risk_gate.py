"""Pre-trade risk gate.

Decision engine sitting between the parser pipeline and the trade
executor. Phase 5 expanded the bare-minimum sanity checks from
Phase 4 with:

* Martingale runtime — the stake is computed as
  ``base * multiplier ** current_streak``, with the streak read from
  :class:`MartingaleState`. The gate is the only place that decides
  the executable stake; the executor never overrides it.
* Daily-loss circuit breaker — refuses new trades when realised P&L
  has already lost more than ``daily_max_loss`` today (UTC).
* Daily-stake cap — refuses when the *committed* stake today
  (placed + pending) is at or above ``daily_max_stake``.
* Concurrency cap — refuses when there are already
  ``max_concurrent_trades`` pending orders.

Decisions are explicit: every blocked signal carries a *reason* so
the dashboard can show why a trade didn't fire.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Literal

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.martingale_state import get_state
from autotrader.models.parser_config import ParserConfig
from autotrader.models.settings import GlobalSettings
from autotrader.models.trade_attempt import TradeAttempt
from autotrader.services.parsers.base import ParsedSignal
from autotrader.services.quotex_manager import AccountMode

Outcome = Literal["allow", "block"]

# Stake sanity bounds — refuse stakes outside this range regardless of
# parser config or martingale ladder.
_MIN_STAKE = 0.01
_MAX_STAKE = 1000.0


def _round_stake(value: float) -> int:
    """Quotex requires integer stakes. Always round UP so the
    operator never under-stakes their intended risk profile —
    base $5 with 85% payout produces $9.25 next-step which we
    must size as $10 (not $9). Mirrors how the dashboard label
    'next $10' would show.
    """
    return math.ceil(value)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    outcome: Outcome
    reason: str
    # Resolved trade-shape after gate transforms (martingale, mode pin).
    stake: float = 0.0
    trade_mode: str = "auto"
    # Which step of the martingale ladder the executor is taking
    # (0 = base, 1 = first recovery, …). Kept on the decision so
    # the executor can store it on the TradeAttempt for the watcher.
    martingale_step: int = 0
    base_stake: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


@dataclass(slots=True)
class RiskBudget:
    """Snapshot of today's spending — fed into the risk gate."""

    realised_pnl: float            # net profit/loss today (UTC)
    committed_stake: float         # placed + pending stakes today
    open_attempts: int             # currently 'pending' rows


def _utc_today_start() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time(0, 0), tzinfo=UTC)


async def compute_budget(session: AsyncSession) -> RiskBudget:
    """Aggregate today's trade attempts into a budget snapshot."""
    today_start = _utc_today_start()

    pnl_stmt = (
        select(func.coalesce(func.sum(TradeAttempt.profit), 0.0))
        .where(TradeAttempt.created_at >= today_start)
        .where(TradeAttempt.profit.is_not(None))  # type: ignore[union-attr]
    )
    realised = float((await session.exec(pnl_stmt)).one() or 0.0)

    committed_stmt = (
        select(func.coalesce(func.sum(TradeAttempt.stake), 0.0))
        .where(TradeAttempt.created_at >= today_start)
        .where(TradeAttempt.status.in_(("pending", "won", "lost")))  # type: ignore[union-attr]
    )
    committed = float((await session.exec(committed_stmt)).one() or 0.0)

    open_stmt = (
        select(func.count())
        .select_from(TradeAttempt)
        .where(TradeAttempt.status == "pending")
    )
    open_count = int((await session.exec(open_stmt)).one() or 0)

    return RiskBudget(
        realised_pnl=realised,
        committed_stake=committed,
        open_attempts=open_count,
    )


async def evaluate(  # noqa: PLR0911, PLR0912  (one branch per failure mode)
    *,
    session: AsyncSession,
    signal: ParsedSignal,
    parser_config: ParserConfig,
    settings: GlobalSettings,
    account_mode: AccountMode,
    live_trading_enabled_env: bool,
    broker_connected: bool,
) -> RiskDecision:
    """Decide whether ``signal`` is safe to send to the broker."""
    # ------------------------------------------------------------------
    # Hard kills.
    # ------------------------------------------------------------------
    if not broker_connected:
        return RiskDecision(outcome="block", reason="broker not connected")
    if not settings.pipeline_active:
        return RiskDecision(outcome="block", reason="pipeline is not active")
    if settings.kill_switch_engaged:
        return RiskDecision(outcome="block", reason="kill switch engaged")
    if not parser_config.enabled:
        return RiskDecision(outcome="block", reason="parser config is disabled")

    # ------------------------------------------------------------------
    # REAL-money gate.
    # ------------------------------------------------------------------
    if account_mode == "REAL" and not live_trading_enabled_env:
        return RiskDecision(
            outcome="block",
            reason=(
                "broker is on REAL but AUTOTRADER_LIVE_TRADING_ENABLED is false"
            ),
        )

    # ------------------------------------------------------------------
    # Trade-mode pin.
    # ------------------------------------------------------------------
    cfg_mode = parser_config.trade_mode if parser_config.trade_mode in (
        "live", "scheduled", "auto",
    ) else "auto"
    effective_mode = cfg_mode
    if cfg_mode == "scheduled" and signal.fire_at is None:
        return RiskDecision(
            outcome="block",
            reason="trade_mode=scheduled but signal has no fire_at",
        )
    if cfg_mode == "live":
        effective_mode = "live"
    elif cfg_mode == "auto":
        effective_mode = "scheduled" if signal.fire_at is not None else "live"

    # ------------------------------------------------------------------
    # Stake — base from signal/config, then martingale-multiplied.
    # ------------------------------------------------------------------
    base_stake = (
        signal.stake if signal.stake is not None else parser_config.default_stake
    )
    martingale_step = 0
    stake = base_stake

    if parser_config.martingale_enabled and parser_config.id is not None:
        state = await get_state(session, parser_config.id)
        martingale_step = state.current_streak
        # Ladder: base * multiplier^step. Step 0 is base; step 1 is
        # the first recovery trade after one loss; etc.
        stake = base_stake * (parser_config.martingale_multiplier ** martingale_step)

    if stake < _MIN_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake:.2f} below minimum {_MIN_STAKE}",
        )
    if stake > _MAX_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake:.2f} above maximum {_MAX_STAKE}",
        )

    # ------------------------------------------------------------------
    # Asset / direction sanity.
    # ------------------------------------------------------------------
    if not signal.asset.strip():
        return RiskDecision(outcome="block", reason="empty asset code")
    if signal.direction not in ("call", "put"):
        return RiskDecision(
            outcome="block",
            reason=f"unrecognised direction {signal.direction!r}",
        )

    # ------------------------------------------------------------------
    # Daily budget + concurrency caps.
    # ------------------------------------------------------------------
    budget = await compute_budget(session)
    if (
        settings.daily_max_loss > 0
        and -budget.realised_pnl >= settings.daily_max_loss
    ):
        return RiskDecision(
            outcome="block",
            reason=(
                f"daily loss limit {settings.daily_max_loss:.2f} reached "
                f"(realised={budget.realised_pnl:.2f})"
            ),
        )
    if (
        settings.daily_max_stake > 0
        and budget.committed_stake + stake > settings.daily_max_stake
    ):
        return RiskDecision(
            outcome="block",
            reason=(
                f"daily stake cap {settings.daily_max_stake:.2f} would be "
                f"exceeded (committed={budget.committed_stake:.2f}, "
                f"new={stake:.2f})"
            ),
        )
    if (
        settings.max_concurrent_trades > 0
        and budget.open_attempts >= settings.max_concurrent_trades
    ):
        return RiskDecision(
            outcome="block",
            reason=(
                f"concurrency cap {settings.max_concurrent_trades} reached "
                f"(open={budget.open_attempts})"
            ),
        )

    return RiskDecision(
        outcome="allow",
        reason="passed",
        stake=stake,
        trade_mode=effective_mode,
        martingale_step=martingale_step,
        base_stake=base_stake,
    )
