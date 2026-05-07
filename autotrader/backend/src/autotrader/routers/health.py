"""Health and version endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from autotrader import __version__
from autotrader.config import settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    live_trading_enabled: bool


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — also exposes a couple of safe runtime flags."""
    return HealthResponse(
        status="ok",
        version=__version__,
        live_trading_enabled=settings.live_trading_enabled,
    )
