"""Singleton row holding the Pyrogram session string + phone, encrypted.

The session string contains MTProto auth keys — full account access.
We encrypt it at rest with the master Fernet key so a stolen DB file
isn't a stolen account. Phone is stored in clear because we only use
it for display / restore hints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from sqlalchemy import LargeBinary
from sqlmodel import Column, Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.crypto import decrypt_str, encrypt
from autotrader.models.base import utc_now


class TelegramSession(SQLModel, table=True):
    """One row, ``id=1``."""

    __tablename__ = "telegram_session"

    id: int = Field(default=1, primary_key=True)
    phone: str = Field(nullable=False)
    session_string_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    user_id: int | None = Field(default=None)
    username: str | None = Field(default=None)
    first_name: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)

    @classmethod
    def from_plain(
        cls,
        *,
        phone: str,
        session_string: str,
        user_id: int | None = None,
        username: str | None = None,
        first_name: str | None = None,
    ) -> Self:
        return cls(
            id=1,
            phone=phone,
            session_string_enc=encrypt(session_string),
            user_id=user_id,
            username=username,
            first_name=first_name,
        )

    def session_string(self) -> str:
        return decrypt_str(self.session_string_enc)


async def load_session(session: AsyncSession) -> TelegramSession | None:
    result = await session.exec(
        select(TelegramSession).where(TelegramSession.id == 1),
    )
    return result.one_or_none()


async def upsert_session(
    session: AsyncSession,
    *,
    phone: str,
    session_string: str,
    user_id: int | None,
    username: str | None,
    first_name: str | None,
) -> TelegramSession:
    existing = await load_session(session)
    if existing is None:
        row = TelegramSession.from_plain(
            phone=phone,
            session_string=session_string,
            user_id=user_id,
            username=username,
            first_name=first_name,
        )
        session.add(row)
    else:
        existing.phone = phone
        existing.session_string_enc = encrypt(session_string)
        existing.user_id = user_id
        existing.username = username
        existing.first_name = first_name
        existing.updated_at = utc_now()
        row = existing
    await session.commit()
    await session.refresh(row)
    return row


async def delete_session(session: AsyncSession) -> bool:
    existing = await load_session(session)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True
