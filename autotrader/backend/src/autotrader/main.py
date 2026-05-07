"""FastAPI entrypoint for the autotrader backend."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from autotrader import __version__
from autotrader.config import settings
from autotrader.db import close_db, init_db
from autotrader.logging_setup import configure_logging
from autotrader.routers import auth, health

# Initialise logging at import time so anything emitted during module
# import (e.g. configuration validation errors) is captured.
configure_logging(settings.log_level)
log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan — bootstraps DB, tears down on shutdown."""
    await init_db()
    log.info(
        "autotrader.startup",
        version=__version__,
        live_trading_enabled=settings.live_trading_enabled,
    )
    try:
        yield
    finally:
        log.info("autotrader.shutdown")
        await close_db()


app = FastAPI(
    title="Autotrader API",
    version=__version__,
    description="Telegram-driven autotrader for pyquotex.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)


# Quiet uvicorn's per-request access logs in production; structlog handles
# everything else. Set AUTOTRADER_LOG_LEVEL=DEBUG to re-enable.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
