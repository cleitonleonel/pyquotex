"""Channels / groups the user has opted into for live message ingestion.

Phase 2 only persists the *flag* + cached metadata (title, type, …)
so the dashboard can list them without round-tripping Pyrogram every
render. Phase 3 wires the actual message handler to this table.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.models.base import utc_now


class WatchedChannel(SQLModel, table=True):
    __tablename__ = "watched_channels"

    chat_id: int = Field(primary_key=True)
    title: str = Field(nullable=False)
    chat_type: str = Field(nullable=False)
    username: str | None = Field(default=None)
    enabled: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)


async def list_watched(session: AsyncSession) -> list[WatchedChannel]:
    result = await session.exec(select(WatchedChannel))
    return list(result.all())


async def upsert_watch(
    session: AsyncSession,
    *,
    chat_id: int,
    title: str,
    chat_type: str,
    username: str | None,
    enabled: bool,
) -> WatchedChannel:
    existing = await session.get(WatchedChannel, chat_id)
    if existing is None:
        row = WatchedChannel(
            chat_id=chat_id,
            title=title,
            chat_type=chat_type,
            username=username,
            enabled=enabled,
        )
        session.add(row)
    else:
        existing.title = title
        existing.chat_type = chat_type
        existing.username = username
        existing.enabled = enabled
        existing.updated_at = utc_now()
        row = existing
    await session.commit()
    await session.refresh(row)
    return row


async def remove_watch(session: AsyncSession, chat_id: int) -> bool:
    existing = await session.get(WatchedChannel, chat_id)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True
