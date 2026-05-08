"""FastAPI dependency-injection helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import AsyncSessionLocal
from autotrader.services.pipeline import Pipeline
from autotrader.services.quotex_manager import QuotexManager
from autotrader.services.telegram_manager import TelegramManager


def get_manager(request: Request) -> QuotexManager:
    """Return the singleton ``QuotexManager`` attached at app startup."""
    manager = getattr(request.app.state, "quotex_manager", None)
    if manager is None:
        raise RuntimeError(
            "QuotexManager is not initialised — lifespan never ran",
        )
    return manager


def get_telegram(request: Request) -> TelegramManager:
    """Return the singleton ``TelegramManager`` attached at app startup."""
    manager = getattr(request.app.state, "telegram_manager", None)
    if manager is None:
        raise RuntimeError(
            "TelegramManager is not initialised — lifespan never ran",
        )
    return manager


def get_pipeline(request: Request) -> Pipeline:
    """Return the singleton ``Pipeline`` attached at app startup."""
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is None:
        raise RuntimeError(
            "Pipeline is not initialised — lifespan never ran",
        )
    return pipeline


async def get_session() -> AsyncIterator[AsyncSession]:
    """One async DB session per request."""
    async with AsyncSessionLocal() as session:
        yield session


# Type aliases used by routers — short and self-documenting.
ManagerDep = Annotated[QuotexManager, Depends(get_manager)]
TelegramDep = Annotated[TelegramManager, Depends(get_telegram)]
PipelineDep = Annotated[Pipeline, Depends(get_pipeline)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
