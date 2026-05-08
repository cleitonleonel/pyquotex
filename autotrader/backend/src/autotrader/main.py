"""FastAPI entrypoint for the autotrader backend."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from autotrader import __version__
from autotrader.config import settings
from autotrader.crypto import CryptoError
from autotrader.db import AsyncSessionLocal, close_db, init_db
from autotrader.logging_setup import configure_logging
from autotrader.models.broker_credentials import (
    delete_credentials,
    load_credentials,
)
from autotrader.models.telegram_session import (
    delete_session as delete_telegram_session,
)
from autotrader.models.telegram_session import (
    load_session as load_telegram_session,
)
from autotrader.routers import auth, broker, health, parsers, telegram
from autotrader.routers import pipeline as pipeline_router
from autotrader.services.executor import TradeExecutor
from autotrader.services.pipeline import Pipeline
from autotrader.services.quotex_manager import QuotexManager
from autotrader.services.telegram_manager import TelegramManager

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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: PLR0915  (linear startup script)
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
        try:
            email = creds.email()
            password = creds.password()
        except CryptoError:
            # Stored row was encrypted with a different Fernet key
            # (rotated, lost, or .env mismatch). The blob is dead
            # weight at this point — drop it so the user can re-enter
            # via the UI instead of facing a perma-broken startup.
            log.error(
                "broker.creds.unreadable",
                detail=(
                    "stored broker credentials cannot be decrypted with the "
                    "current AUTOTRADER_FERNET_KEY — clearing the row; "
                    "re-enter credentials in the dashboard"
                ),
            )
            async with AsyncSessionLocal() as session:
                await delete_credentials(session)
        else:
            mode = (
                creds.account_mode
                if creds.account_mode in ("PRACTICE", "REAL")
                else "PRACTICE"
            )
            manager.set_credentials(email, password, mode)
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

    # Telegram manager + session restore. Phase 2 just keeps the
    # client warm; Phase 3 attaches the live message handler.
    telegram_manager = TelegramManager()
    app.state.telegram_manager = telegram_manager
    async with AsyncSessionLocal() as session:
        tg_row = await load_telegram_session(session)
    if tg_row is not None:
        try:
            session_string = tg_row.session_string()
        except CryptoError:
            log.error(
                "telegram.session.unreadable",
                detail=(
                    "stored Telegram session cannot be decrypted with the "
                    "current AUTOTRADER_FERNET_KEY — clearing the row; "
                    "re-login from the dashboard"
                ),
            )
            async with AsyncSessionLocal() as session:
                await delete_telegram_session(session)
        else:
            try:
                ok = await telegram_manager.restore(
                    session_string,
                    phone=tg_row.phone,
                )
                if not ok:
                    # Session no longer valid (e.g. user revoked it from
                    # the Telegram app). Drop the row so the user is
                    # forced through a fresh login.
                    async with AsyncSessionLocal() as session:
                        await delete_telegram_session(session)
                    log.warning("telegram.restore.invalidated_row")
            except Exception as exc:  # pragma: no cover  (best-effort)
                log.warning("telegram.restore.failed", error=str(exc))

    # Execution pipeline + executor. The pipeline is wired immediately
    # but the master switch (GlobalSettings.pipeline_active) and the
    # per-config ``enabled`` flag still gate every dispatch — flipping
    # the env flag alone never auto-trades.
    executor = TradeExecutor(
        manager=manager,
        live_trading_enabled_env=settings.live_trading_enabled,
    )
    pipeline = Pipeline(manager=manager, executor=executor)
    app.state.pipeline = pipeline
    app.state.executor = executor
    telegram_manager.set_message_callback(pipeline.dispatch)

    log.info(
        "autotrader.startup",
        version=__version__,
        live_trading_enabled=settings.live_trading_enabled,
    )
    try:
        yield
    finally:
        log.info("autotrader.shutdown")
        telegram_manager.set_message_callback(None)
        await executor.shutdown()
        await manager.disconnect()
        with contextlib.suppress(Exception):
            await telegram_manager._teardown_client()
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
app.include_router(telegram.router)
app.include_router(parsers.router)
app.include_router(pipeline_router.router)


# Quiet uvicorn's per-request access logs in production; structlog handles
# everything else. Set AUTOTRADER_LOG_LEVEL=DEBUG to re-enable.
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
