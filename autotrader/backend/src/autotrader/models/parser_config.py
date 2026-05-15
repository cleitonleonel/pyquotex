"""Parser configurations — multiple per channel.

Each watched chat can host many named parser configs ranked by
``priority`` (lower runs first). Phase 4's executor walks them in
priority order, and every enabled parser that emits a signal fires
its own trade — sibling parsers on a chat are independent
subscribers, not alternatives.

In addition to the parsing config itself we persist the trade-shaping
knobs the user picks per parser:

* ``trade_mode`` — ``live`` / ``scheduled`` / ``auto``. ``auto`` is the
  default: a signal that carries a ``fire_at`` schedules a pending
  order, otherwise it fires immediately. ``live`` and ``scheduled``
  are pin overrides — useful when a single channel mixes signal
  styles and the user wants strict separation per parser.
* Martingale block (enabled / multiplier / max-streak / reset-on-win)
  for losing-streak recovery sizing. Phase 4's executor reads this
  config; the running streak counter lives in a separate state row.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.base import utc_now

TradeMode = Literal["live", "scheduled", "auto"]


class ParserConfig(SQLModel, table=True):
    """One row per parser; many rows can share the same ``chat_id``."""

    __tablename__ = "parser_configs"

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(index=True, nullable=False)

    # Display
    name: str = Field(default="", nullable=False)
    priority: int = Field(default=100, nullable=False)  # lower runs first

    # Parsing
    parser_type: str = Field(default="template", nullable=False)
    parser_config_json: str = Field(default="{}", nullable=False)
    timezone: str = Field(default="UTC", nullable=False)
    timezone_offset_minutes: int = Field(default=0, nullable=False)
    asset_aliases_json: str = Field(default="{}", nullable=False)
    aggregate_window_seconds: int = Field(default=0, nullable=False)

    # Trade shaping
    default_stake: float = Field(default=1.0, nullable=False)
    default_duration_seconds: int = Field(default=60, nullable=False)
    trade_mode: str = Field(default="auto", nullable=False)

    # Martingale (config only; runtime streak state is separate).
    martingale_enabled: bool = Field(default=False, nullable=False)
    martingale_multiplier: float = Field(default=2.0, nullable=False)
    martingale_max_streak: int = Field(default=5, nullable=False)
    martingale_reset_on_win: bool = Field(default=True, nullable=False)
    # When True, a *losing* trade triggers an auto-recovery trade with
    # the same asset / direction / duration and a stake bumped by the
    # martingale ladder (``base × multiplier^current_streak``). This
    # mirrors how binary-options signal channels phrase their gale
    # rules (e.g. *"IF LOSS TAKE 1 STEP MTG (Same Direction Double
    # Amount)"*) — the channel doesn't repeat the signal, it expects
    # the bot to fire the recovery itself.
    #
    # Defaults to False so the upstream "stake-multiplier on next
    # channel signal" semantic stays the only thing existing configs
    # see until an operator opts in. ``martingale_enabled`` must be
    # True for this to do anything; both flags off = flat staking.
    martingale_auto_recovery: bool = Field(default=False, nullable=False)

    # Winning-streak (Paroli) sizing — opt-in per parser. When True,
    # after a winning trade settles, the **next channel signal** for
    # this parser stakes at ``ceil(martingale_state.last_payout)``
    # instead of base. Compounds up to ``winning_streak_max_level``
    # consecutive wins, then resets to base. A loss at any point also
    # resets to base (loss handling delegates to martingale config).
    #
    # ``last_payout`` (live runtime value) lives on
    # :class:`MartingaleState`; both ladders share that table since
    # they're per-parser counters with identical lifecycle (reset on
    # config edit / manual reset button).
    winning_streak_enabled: bool = Field(default=False, nullable=False)
    winning_streak_max_level: int = Field(default=2, nullable=False)

    enabled: bool = Field(default=True, nullable=False)

    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)

    # ------------------------------------------------------------------
    # JSON convenience accessors
    # ------------------------------------------------------------------

    def parser_config_dict(self) -> dict[str, Any]:
        try:
            value = json.loads(self.parser_config_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def asset_aliases(self) -> dict[str, str]:
        try:
            value = json.loads(self.asset_aliases_json or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def list_configs(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
) -> list[ParserConfig]:
    stmt = select(ParserConfig)
    if chat_id is not None:
        stmt = stmt.where(ParserConfig.chat_id == chat_id)
    stmt = stmt.order_by(ParserConfig.chat_id, ParserConfig.priority, ParserConfig.id)  # type: ignore[arg-type]
    result = await session.exec(stmt)
    return list(result.all())


async def get_config(session: AsyncSession, config_id: int) -> ParserConfig | None:
    return await session.get(ParserConfig, config_id)


async def create_config(
    session: AsyncSession,
    *,
    chat_id: int,
    payload: dict[str, Any],
) -> ParserConfig:
    row = ParserConfig(
        chat_id=chat_id,
        parser_config_json=json.dumps(payload.get("parser_config", {})),
        asset_aliases_json=json.dumps(payload.get("asset_aliases", {})),
        **{
            k: v
            for k, v in payload.items()
            if k
            not in ("parser_config", "asset_aliases")
            and hasattr(ParserConfig, k)
            and k not in ("id", "chat_id", "created_at", "updated_at")
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_config(
    session: AsyncSession,
    *,
    config_id: int,
    payload: dict[str, Any],
) -> ParserConfig | None:
    existing = await get_config(session, config_id)
    if existing is None:
        return None
    for key, value in payload.items():
        if key == "parser_config":
            existing.parser_config_json = json.dumps(value)
        elif key == "asset_aliases":
            existing.asset_aliases_json = json.dumps(value)
        elif key in ("id", "chat_id", "created_at", "updated_at"):
            continue
        elif hasattr(ParserConfig, key):
            setattr(existing, key, value)
    existing.updated_at = utc_now()
    await session.commit()
    await session.refresh(existing)
    return existing


async def delete_config(session: AsyncSession, config_id: int) -> bool:
    existing = await get_config(session, config_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True
