"""FastAPI dependency-injection helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import AsyncSessionLocal
from autotrader.services.quotex_manager import QuotexManager


def get_manager(request: Request) -> QuotexManager:
    """Return the singleton ``QuotexManager`` attached at app startup."""
    manager = getattr(request.app.state, "quotex_manager", None)
    if manager is None:
        raise RuntimeError(
            "QuotexManager is not initialised — lifespan never ran",
        )
    return manager


async def get_session() -> AsyncIterator[AsyncSession]:
    """One async DB session per request."""
    async with AsyncSessionLocal() as session:
        yield session


# Type aliases used by routers — short and self-documenting.
ManagerDep = Annotated[QuotexManager, Depends(get_manager)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
