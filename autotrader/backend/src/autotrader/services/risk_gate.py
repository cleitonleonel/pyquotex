"""Pre-trade risk gate.

Pure-function decision engine sitting between the parser pipeline and
the trade executor. Phase 4 ships the bare-minimum sanity checks; the
full risk module (daily loss limits, position sizing, asset
whitelist/blacklist, drawdown auto-pause) lands in Phase 5 and will
plug into this same surface.

Decisions are explicit: every blocked signal carries a *reason* so the
dashboard can show why a trade didn't fire — silent dropping is a
debugging nightmare.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from autotrader.models.parser_config import ParserConfig
from autotrader.models.settings import GlobalSettings
from autotrader.services.parsers.base import ParsedSignal
from autotrader.services.quotex_manager import AccountMode

Outcome = Literal["allow", "block"]

# Stake sanity bounds — refuse stakes outside this range regardless of
# parser config. Phase 5 makes this user-configurable.
_MIN_STAKE = 0.01
_MAX_STAKE = 1000.0


@dataclass(frozen=True, slots=True)
class RiskDecision:
    outcome: Outcome
    reason: str
    # Resolved trade-shape after gate transforms (e.g. trade_mode pin).
    stake: float = 0.0
    trade_mode: str = "auto"

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


def evaluate(  # noqa: PLR0911  (one return per failure mode)
    *,
    signal: ParsedSignal,
    parser_config: ParserConfig,
    settings: GlobalSettings,
    account_mode: AccountMode,
    live_trading_enabled_env: bool,
    broker_connected: bool,
) -> RiskDecision:
    """Decide whether ``signal`` is safe to send to the broker.

    The caller passes the *current* ``GlobalSettings`` row (so the
    kill switch / pipeline-active flips take effect without restart),
    the broker's account mode + whether REAL is gated by env, and
    whether the broker is actually connected.
    """
    # ------------------------------------------------------------------
    # Hard kills — no signal should fire under these conditions.
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
    # REAL-money gate — env flag is the master switch even when the
    # broker is logged into a real account.
    # ------------------------------------------------------------------
    if account_mode == "REAL" and not live_trading_enabled_env:
        return RiskDecision(
            outcome="block",
            reason=(
                "broker is on REAL but AUTOTRADER_LIVE_TRADING_ENABLED is false"
            ),
        )

    # ------------------------------------------------------------------
    # Trade-mode pin (live / scheduled / auto).
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
        # Live forces immediate execution even if the parser produced a
        # fire_at — the executor reads ``trade_mode`` from this decision.
        effective_mode = "live"
    elif cfg_mode == "auto":
        effective_mode = "scheduled" if signal.fire_at is not None else "live"

    # ------------------------------------------------------------------
    # Stake — signal override OR parser default; sanity-bound.
    # ------------------------------------------------------------------
    stake = signal.stake if signal.stake is not None else parser_config.default_stake
    if stake < _MIN_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake} below minimum {_MIN_STAKE}",
        )
    if stake > _MAX_STAKE:
        return RiskDecision(
            outcome="block",
            reason=f"stake {stake} above maximum {_MAX_STAKE}",
        )

    # ------------------------------------------------------------------
    # Asset sanity — empty asset means the parser couldn't resolve.
    # ------------------------------------------------------------------
    if not signal.asset.strip():
        return RiskDecision(outcome="block", reason="empty asset code")

    # ------------------------------------------------------------------
    # Direction sanity (the normaliser already enforces it; check anyway).
    # ------------------------------------------------------------------
    if signal.direction not in ("call", "put"):
        return RiskDecision(
            outcome="block",
            reason=f"unrecognised direction {signal.direction!r}",
        )

    return RiskDecision(
        outcome="allow",
        reason="passed",
        stake=stake,
        trade_mode=effective_mode,
    )
