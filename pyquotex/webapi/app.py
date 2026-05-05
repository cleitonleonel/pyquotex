"""FastAPI application factory + lifespan management."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .relays import StreamRelay
from .routers import account as account_router
from .routers import auth as auth_router
from .routers import market as market_router
from .routers import streams as streams_router
from .routers import trades as trades_router

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Spin up a single shared :class:`Quotex` client and tear it down
    cleanly on shutdown.

    The connect attempt happens lazily on the first auth/connect call
    (or on the first endpoint that uses the client) — we DON'T block
    startup on broker reachability so the container can boot even if
    the broker is briefly unreachable. Health probes will report
    ``connected=false`` until the first successful connect.
    """
    from pyquotex.stable_api import Quotex

    settings: Settings = app.state.settings

    client = Quotex(
        email=settings.email,
        password=settings.password,
        lang=settings.lang,
    )
    client.set_account_mode(settings.account_mode)

    relay = StreamRelay(
        client,
        poll_interval=settings.relay_poll_interval,
        queue_max=settings.relay_queue_max,
    )

    app.state.quotex_client = client
    app.state.stream_relay = relay
    logger.info(
        "pyquotex web API starting (host=%s port=%s account_mode=%s)",
        settings.host, settings.port, settings.account_mode,
    )

    try:
        yield
    finally:
        logger.info("pyquotex web API shutting down — closing client")
        try:
            await relay.shutdown()
        except Exception as e:
            logger.warning("relay shutdown failed: %s", e)
        try:
            await client.close()
        except Exception as e:
            logger.warning("client close failed: %s", e)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a fully-wired FastAPI app.

    Pass ``settings`` to override env-var loading (useful for tests).
    """
    if settings is None:
        settings = Settings.from_env()

    app = FastAPI(
        title="pyquotex web API",
        description=(
            "REST + WebSocket API on top of pyquotex. Single-tenant: "
            "one shared broker session serves every request. Auth via "
            "``X-API-Key`` header or ``Authorization: Bearer …``."
        ),
        version="1.4.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Public (no auth) — health probe
    app.include_router(account_router.public_router)
    # Auth-gated
    app.include_router(auth_router.router)
    app.include_router(account_router.router)
    app.include_router(market_router.router)
    app.include_router(trades_router.router)
    # WebSocket
    app.include_router(streams_router.router)

    return app
