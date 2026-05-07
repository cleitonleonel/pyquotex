"""FastAPI entrypoint for the autotrader backend."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from autotrader import __version__
from autotrader.config import settings
from autotrader.db import AsyncSessionLocal, close_db, init_db
from autotrader.logging_setup import configure_logging
from autotrader.models.broker_credentials import load_credentials
from autotrader.routers import auth, broker, health
from autotrader.services.quotex_manager import QuotexManager

# Initialise logging at import time so anything emitted during module
# import (e.g. configuration validation errors) is captured.
configure_logging(settings.log_level)
log = structlog.get_logger(__name__)


def _broker_root_path() -> str:
    """Where pyquotex writes session.json / settings/.

    Mirror the SQLite parent dir so all stateful files live on the same
    Docker volume — restarts pick up the cached SSID + cookies for
    free, skipping a full re-auth round-trip.
    """
    db_url = settings.db_url
    if db_url.startswith("sqlite") and ":memory:" not in db_url:
        db_path = db_url.split("///", 1)[-1]
        if db_path:
            return str(Path(db_path).parent.resolve())
    return "."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bootstraps DB + Quotex client; tears down on shutdown."""
    await init_db()

    manager = QuotexManager(root_path=_broker_root_path())
    app.state.quotex_manager = manager

    # Auto-load credentials and pre-warm the connection so the first
    # trade after startup pays no login cost. If the broker requests
    # an OTP we leave the connect parked in ``awaiting_otp`` — the
    # user can finish it from the dashboard.
    async with AsyncSessionLocal() as session:
        creds = await load_credentials(session)
    if creds is not None:
        mode = creds.account_mode if creds.account_mode in ("PRACTICE", "REAL") else "PRACTICE"
        manager.set_credentials(creds.email(), creds.password(), mode)
        try:
            manager.begin_connect()
            await manager.wait_settled(timeout=2.0)
            log.info(
                "broker.autoconnect",
                state=manager.status().state,
                last_error=manager.status().last_error,
            )
        except Exception as exc:  # pragma: no cover  (best-effort warm-up)
            log.warning("broker.autoconnect.failed", error=str(exc))

    log.info(
        "autotrader.startup",
        version=__version__,
        live_trading_enabled=settings.live_trading_enabled,
    )
    try:
        yield
    finally:
        log.info("autotrader.shutdown")
        await manager.disconnect()
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
app.include_router(broker.router)


# Quiet uvicorn's per-request access logs in production; structlog handles
# everything else. Set AUTOTRADER_LOG_LEVEL=DEBUG to re-enable.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
