"""Singleton row for the broker (Quotex) email + password.

Both fields are encrypted at rest with the master Fernet key from
``settings.fernet_key``. We never log them, never expose them on any
API surface — only a masked-email projection ever leaves the manager.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from sqlalchemy import LargeBinary
from sqlmodel import Column, Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.crypto import decrypt_str, encrypt
from autotrader.models.base import utc_now


class BrokerCredentials(SQLModel, table=True):
    """Singleton row, ``id=1``."""

    __tablename__ = "broker_credentials"

    id: int = Field(default=1, primary_key=True)
    email_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    password_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))

    # Last user-selected account mode. Strict literal at the service
    # layer; stored loose here so we don't need a DB migration when
    # adding tournament/team modes later.
    account_mode: str = Field(default="PRACTICE")

    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)

    # ------------------------------------------------------------------
    # Convenience constructors / accessors
    # ------------------------------------------------------------------

    @classmethod
    def from_plain(
        cls,
        email: str,
        password: str,
        account_mode: str = "PRACTICE",
    ) -> Self:
        return cls(
            id=1,
            email_enc=encrypt(email),
            password_enc=encrypt(password),
            account_mode=account_mode,
        )

    def email(self) -> str:
        return decrypt_str(self.email_enc)

    def password(self) -> str:
        return decrypt_str(self.password_enc)


async def load_credentials(session: AsyncSession) -> BrokerCredentials | None:
    result = await session.exec(select(BrokerCredentials).where(BrokerCredentials.id == 1))
    return result.one_or_none()


async def upsert_credentials(
    session: AsyncSession,
    email: str,
    password: str,
    account_mode: str = "PRACTICE",
) -> BrokerCredentials:
    """Insert or replace the singleton row."""
    existing = await load_credentials(session)
    if existing is None:
        row = BrokerCredentials.from_plain(email, password, account_mode)
        session.add(row)
    else:
        existing.email_enc = encrypt(email)
        existing.password_enc = encrypt(password)
        existing.account_mode = account_mode
        existing.updated_at = utc_now()
        row = existing
    await session.commit()
    await session.refresh(row)
    return row


async def delete_credentials(session: AsyncSession) -> bool:
    existing = await load_credentials(session)
    if existing is None:
        return False
    await session.delete(existing)
    await session.commit()
    return True
