"""Shared fixtures: a FastAPI app with the broker dependencies replaced
by mocks via FastAPI's ``app.dependency_overrides`` (the canonical way
to inject test doubles, since the lifespan would otherwise hit the
live broker)."""
from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from pyquotex.global_value import AuthStatus, WebsocketStatus
from pyquotex.webapi.config import Settings
from pyquotex.webapi.deps import get_client, get_relay, get_settings
from pyquotex.webapi.relays import StreamRelay
from pyquotex.webapi.routers import account as account_router
from pyquotex.webapi.routers import auth as auth_router
from pyquotex.webapi.routers import market as market_router
from pyquotex.webapi.routers import streams as streams_router
from pyquotex.webapi.routers import trades as trades_router

API_KEY = "test-api-key-12345"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        email="test@test.com",
        password="x",
        api_key=API_KEY,
        account_mode="PRACTICE",
        result_poll_timeout=2.0,
        relay_poll_interval=0.02,
    )


@pytest.fixture
def fake_quotex():
    client = MagicMock()
    api = MagicMock()
    api.state = SimpleNamespace(
        status=WebsocketStatus.CONNECTED,
        auth_status=AuthStatus.AUTHENTICATED,
        websocket_error_reason=None,
    )
    api._active_pending = {}
    api.pending_id = None
    api.reconnect_supervisor = MagicMock()
    api.reconnect_supervisor.stats = SimpleNamespace(
        attempts=0, successful_reconnects=0, last_reconnect_at=None,
    )
    client.api = api

    client.connect = AsyncMock(return_value=(True, "Connected"))
    client.close = AsyncMock(return_value=True)
    client.set_account_mode = MagicMock()
    client.check_connect = AsyncMock(return_value=True)

    profile = SimpleNamespace(
        profile_id=42, nick_name="tester",
        currency_code="USD", currency_symbol="$",
        country_name="X", offset=0,
        demo_balance=10000.0, live_balance=0.0,
    )
    client.get_profile = AsyncMock(return_value=profile)
    client.get_balance = AsyncMock(return_value=10000.0)

    candles = [
        {"time": 1700000000 + i * 60, "open": 1.0, "close": 1.01,
         "high": 1.02, "low": 0.99}
        for i in range(5)
    ]
    client.get_candles = AsyncMock(return_value=candles)
    client.get_historical_candles = AsyncMock(return_value=candles)

    client.start_realtime_sentiment = AsyncMock(return_value={
        "asset": "EURUSD", "bullish": 0.65, "bearish": 0.35,
    })
    client.get_realtime_sentiment = AsyncMock(return_value={
        "bullish": 0.65, "bearish": 0.35,
    })

    client.start_realtime_price = AsyncMock(return_value={"EURUSD": deque()})

    realtime_price_state = {
        "EURUSD": deque([
            {"time": 1700000000, "price": 1.10},
            {"time": 1700000001, "price": 1.11},
        ]),
    }
    async def fake_get_realtime_price(asset):
        return realtime_price_state.get(asset, deque())
    client.get_realtime_price = AsyncMock(side_effect=fake_get_realtime_price)
    client._realtime_price_state = realtime_price_state

    client.stop_candles_stream = AsyncMock()

    client.buy = AsyncMock(return_value=(True, {"id": "trade-1", "asset": "EURUSD"}))

    async def fake_open_pending(*a, **kw):
        api.pending_id = "pending-1"
        return (True, {"pending_id": "pending-1"})
    client.open_pending = AsyncMock(side_effect=fake_open_pending)
    client.open_pending_at_price = AsyncMock(side_effect=fake_open_pending)

    client.wait_for_order_close = AsyncMock(return_value=("win", 0.94))
    client.sell_option = AsyncMock(return_value={"sold": True})

    return client


@asynccontextmanager
async def _noop_lifespan(app: FastAPI):
    """Lifespan that does nothing — the real broker connect would
    hit the network."""
    yield


@pytest.fixture
def app(settings, fake_quotex):
    """Build a FastAPI app exactly like ``create_app()`` would, but
    swap the lifespan for a no-op and inject the mock client + relay
    via ``dependency_overrides``."""
    app = FastAPI(
        title="pyquotex web API (test)",
        version="test",
        lifespan=_noop_lifespan,
    )
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(account_router.public_router)
    app.include_router(auth_router.router)
    app.include_router(account_router.router)
    app.include_router(market_router.router)
    app.include_router(trades_router.router)
    app.include_router(streams_router.router)

    relay = StreamRelay(
        fake_quotex,
        poll_interval=settings.relay_poll_interval,
        queue_max=settings.relay_queue_max,
    )

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_client] = lambda: fake_quotex
    app.dependency_overrides[get_relay] = lambda: relay

    # WebSocket endpoints can't use Depends() for path/query params,
    # so they read state via Request → app.state. Mirror it there too.
    app.state.quotex_client = fake_quotex
    app.state.stream_relay = relay
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
