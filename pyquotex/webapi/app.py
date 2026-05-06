"""FastAPI application factory + lifespan management."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .otp import OtpManager
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

    The :class:`OtpManager` is wired into ``Quotex`` as the
    ``on_otp_callback`` so that broker-emailed PIN prompts route
    through ``POST /auth/otp`` instead of trying to read from stdin
    (which would hang inside a container).
    """
    from pyquotex.stable_api import Quotex

    settings: Settings = app.state.settings

    otp_manager = OtpManager(timeout=settings.otp_timeout)

    client = Quotex(
        email=settings.email,
        password=settings.password,
        lang=settings.lang,
        on_otp_callback=otp_manager.callback,
    )
    client.set_account_mode(settings.account_mode)

    relay = StreamRelay(
        client,
        poll_interval=settings.relay_poll_interval,
        queue_max=settings.relay_queue_max,
    )

    app.state.quotex_client = client
    app.state.stream_relay = relay
    app.state.otp_manager = otp_manager
    # Tracks the in-flight ``Quotex.connect()`` task so /auth/connect
    # is non-blocking and /auth/otp can join on it.
    app.state.connect_task = None
    logger.info(
        "pyquotex web API starting (host=%s port=%s account_mode=%s)",
        settings.host, settings.port, settings.account_mode,
    )

    try:
        yield
    finally:
        logger.info("pyquotex web API shutting down — closing client")
        # Cancel any in-flight connect so the lifespan exits cleanly.
        task = getattr(app.state, "connect_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        otp_manager.reset()
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
