"""Per-channel parser configuration.

Each watched Telegram channel can have its own parser type (template
or regex), parser-specific config, timezone, asset alias map, default
stake / duration, and an optional multi-message aggregation window.

We store the parser-specific config and the asset alias map as JSON
columns so the schema doesn't need to grow when we add a parser type
later.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.base import utc_now


class ParserConfig(SQLModel, table=True):
    """Singleton-per-channel — keyed by ``chat_id``."""

    __tablename__ = "parser_configs"

    chat_id: int = Field(primary_key=True)

    parser_type: str = Field(default="template", nullable=False)
    parser_config_json: str = Field(default="{}", nullable=False)

    timezone: str = Field(default="UTC", nullable=False)
    timezone_offset_minutes: int = Field(default=0, nullable=False)

    asset_aliases_json: str = Field(default="{}", nullable=False)

    default_stake: float = Field(default=1.0, nullable=False)
    default_duration_seconds: int = Field(default=60, nullable=False)

    # 0 = single-message; > 0 = sliding-window multi-message aggregation.
    aggregate_window_seconds: int = Field(default=0, nullable=False)

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


async def list_configs(session: AsyncSession) -> list[ParserConfig]:
    result = await session.exec(select(ParserConfig))
    return list(result.all())


async def get_config(session: AsyncSession, chat_id: int) -> ParserConfig | None:
    return await session.get(ParserConfig, chat_id)


async def upsert_config(
    session: AsyncSession,
    *,
    chat_id: int,
    parser_type: str,
    parser_config: dict[str, Any],
    timezone: str,
    timezone_offset_minutes: int,
    asset_aliases: dict[str, str],
    default_stake: float,
    default_duration_seconds: int,
    aggregate_window_seconds: int,
    enabled: bool,
) -> ParserConfig:
    existing = await get_config(session, chat_id)
    payload = {
        "parser_type": parser_type,
        "parser_config_json": json.dumps(parser_config),
        "timezone": timezone,
        "timezone_offset_minutes": timezone_offset_minutes,
        "asset_aliases_json": json.dumps(asset_aliases),
        "default_stake": default_stake,
        "default_duration_seconds": default_duration_seconds,
        "aggregate_window_seconds": aggregate_window_seconds,
        "enabled": enabled,
    }
    if existing is None:
        row = ParserConfig(chat_id=chat_id, **payload)
        session.add(row)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
        existing.updated_at = utc_now()
        row = existing
    await session.commit()
    await session.refresh(row)
    return row


async def delete_config(session: AsyncSession, chat_id: int) -> bool:
    existing = await get_config(session, chat_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True
