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

    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
