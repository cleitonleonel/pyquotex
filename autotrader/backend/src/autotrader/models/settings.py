"""Singleton row holding global runtime settings.

The DB row stores knobs the dashboard can flip at runtime (default stake,
default duration, kill switch). The *real-money* gate is intentionally
NOT in here — it's env-only so a misconfigured / corrupted DB cannot
enable live trading on its own.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from autotrader.models.base import utc_now


class GlobalSettings(SQLModel, table=True):
    """One row, ``id=1``. Loaded on startup, mutated via the settings API."""

    __tablename__ = "global_settings"

    id: int = Field(default=1, primary_key=True)

    # Sensible defaults parsers can fall back to when the signal omits them.
    default_stake: float = Field(default=1.0)
    default_duration_seconds: int = Field(default=60)

    # Hard pause: when true, the execution pipeline drops every signal.
    kill_switch_engaged: bool = Field(default=False)

    # Master switch for the live execution pipeline. Default is FALSE
    # so a stock install never auto-trades — flip it from the dashboard
    # only after parsers are dialled in. Independent of (and AND-ed
    # with) the ``AUTOTRADER_LIVE_TRADING_ENABLED`` env gate.
    pipeline_active: bool = Field(default=False)

    # Risk module (Phase 5).
    # ``0`` for any of these means "no cap".
    daily_max_loss: float = Field(default=0.0, nullable=False)
    daily_max_stake: float = Field(default=0.0, nullable=False)
    max_concurrent_trades: int = Field(default=0, nullable=False)

    # ------------------------------------------------------------------
    # Admin Telegram bot (Phase 8).
    # ------------------------------------------------------------------
    # The Telegram user_id of the bound admin. ``None`` = unbound; the
    # first /start the bot receives writes the sender's user_id here,
    # and from that point only that user_id can issue commands. Cleared
    # by /unbind (from the bot or the dashboard) so the next /start can
    # re-bind.
    admin_telegram_user_id: int | None = Field(default=None, nullable=True)

    # Per-class notification toggles. Operators can mute a single class
    # via ``/notify <class> off`` from the bot. Defaults to ON for all
    # four so a fresh bind immediately sees the firehose.
    admin_notify_placed: bool = Field(default=True, nullable=False)
    admin_notify_settled: bool = Field(default=True, nullable=False)
    admin_notify_risk_rejected: bool = Field(default=True, nullable=False)
    admin_notify_system_error: bool = Field(default=True, nullable=False)

    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
