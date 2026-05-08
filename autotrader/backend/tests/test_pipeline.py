"""End-to-end Phase 4 pipeline tests.

Boots the real FastAPI app, swaps out ``Quotex`` with the
``FakeQuotex`` from test_broker (which captures buy / open_pending
calls), seeds a watched chat + parser config + broker session, then
flips the master switch and dispatches synthetic Telegram messages
through the pipeline. Asserts:

  * a live signal causes ``buy()`` to fire with the right shape;
  * a scheduled signal causes ``open_pending()`` to fire instead;
  * a batch parser produces N pending orders from one message;
  * the master switch / kill switch / disabled-config gates all block;
  * persisted ``TradeAttempt`` rows match the captured calls.

Pyrogram is *not* exercised — the pipeline is invoked directly via
``app.state.pipeline.dispatch(...)`` so the test stays deterministic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_broker import FakeQuotex


@pytest.fixture(autouse=True)
def _reset_fake_quotex_state() -> Iterator[None]:
    """Class-level FakeQuotex state must be wiped between tests."""
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []
    yield


@pytest.fixture
def fake_quotex(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeQuotex]]:
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )
    yield FakeQuotex


@pytest.fixture
def app_client(fake_quotex: type[FakeQuotex]) -> Iterator[TestClient]:
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.broker_credentials import BrokerCredentials  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    with TestClient(app) as c:
        yield c

    from sqlmodel import delete  # noqa: PLC0415

    async def _wipe() -> None:
        manager = app.state.quotex_manager
        await manager.cancel_connect()
        if manager.connected:
            await manager.disconnect()
        manager.clear_credentials()
        async with AsyncSessionLocal() as s:
            await s.exec(delete(BrokerCredentials))  # type: ignore[call-overload]
            await s.exec(delete(ParserConfig))  # type: ignore[call-overload]
            await s.exec(delete(TradeAttempt))  # type: ignore[call-overload]
            await s.exec(delete(WatchedChannel))  # type: ignore[call-overload]
            await s.exec(delete(GlobalSettings))  # type: ignore[call-overload]
            await s.commit()
        # Clear the pipeline's parser cache so next test starts fresh.
        app.state.pipeline.invalidate_all()
        await engine.dispose()

    asyncio.new_event_loop().run_until_complete(_wipe())


def _login(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"passcode": "test-passcode"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _connect_broker(client: TestClient, headers: dict[str, str]) -> None:
    client.put(
        "/broker/credentials",
        headers=headers,
        json={"email": "x@y.com", "password": "p"},
    )
    client.post("/broker/connect", headers=headers)


def _add_watch(client: TestClient, headers: dict[str, str], chat_id: int) -> None:
    client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": chat_id,
            "title": "Signals",
            "chat_type": "channel",
            "username": "signals",
            "enabled": True,
        },
    )


def _create_parser(
    client: TestClient,
    headers: dict[str, str],
    *,
    chat_id: int,
    parser_type: str,
    parser_config: dict,
    **overrides: object,
) -> int:
    body: dict[str, object] = {
        "chat_id": chat_id,
        "name": "test",
        "priority": 100,
        "parser_type": parser_type,
        "parser_config": parser_config,
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {},
        "default_stake": 5.0,
        "default_duration_seconds": 60,
        "trade_mode": "auto",
        "aggregate_window_seconds": 0,
        "martingale": {
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
        },
        "enabled": True,
    }
    body.update(overrides)
    r = client.post("/parsers/configs", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _dispatch(client: TestClient, *, chat_id: int, text: str) -> None:
    """Push a synthetic message through the pipeline directly."""
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415

    pipeline = client.app.state.pipeline  # type: ignore[attr-defined]
    await pipeline.dispatch(
        RawMessage(
            text=text,
            chat_id=chat_id,
            sender_id=200,
            received_at=datetime.now(UTC),
        ),
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===========================================================================
# Master switch off → no trades
# ===========================================================================


def test_pipeline_inactive_drops_signals(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )

    # pipeline_active is False by default — dispatch should no-op.
    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))

    assert FakeQuotex.buy_calls == []
    r = app_client.get("/pipeline/trades", headers=headers)
    assert r.json() == []


# ===========================================================================
# Live signal → buy() fired
# ===========================================================================


def _activate(app_client: TestClient, headers: dict[str, str]) -> None:
    """Flip the master switch directly via the DB.

    The /pipeline/activate endpoint requires Telegram to be logged
    in (defence in depth — auto-trading without an inbound message
    source is meaningless). Tests use a fake message dispatch so we
    bypass that prereq by writing the flag directly.
    """
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.settings import GlobalSettings  # noqa: PLC0415

    async def _flip() -> None:
        async with AsyncSessionLocal() as s:
            row = await s.get(GlobalSettings, 1) or GlobalSettings(id=1)
            row.pipeline_active = True
            s.add(row)
            await s.commit()

    asyncio.new_event_loop().run_until_complete(_flip())


def test_live_signal_fires_buy(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        default_stake=10.0,
    )
    _activate(app_client, headers)

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))

    assert len(FakeQuotex.buy_calls) == 1
    call = FakeQuotex.buy_calls[0]
    assert call["asset"] == "EURUSD"
    assert call["direction"] == "call"
    assert call["duration"] == 60
    assert call["amount"] == 10.0
    assert FakeQuotex.pending_calls == []

    r = app_client.get("/pipeline/trades", headers=headers)
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "pending"
    assert body[0]["broker_order_id"] == "order-1"
    assert body[0]["trade_mode"] == "live"


# ===========================================================================
# Scheduled signal → open_pending() fired
# ===========================================================================


def test_scheduled_signal_fires_open_pending(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION} at {TIME}"},
        trade_mode="scheduled",
    )
    _activate(app_client, headers)

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m at 23:59"))

    assert len(FakeQuotex.pending_calls) == 1
    call = FakeQuotex.pending_calls[0]
    assert call["asset"] == "EURUSD"
    assert call["direction"] == "call"
    assert call["duration"] == 60
    assert call["open_time"] is not None
    assert FakeQuotex.buy_calls == []

    r = app_client.get("/pipeline/trades", headers=headers)
    assert r.json()[0]["trade_mode"] == "scheduled"


# ===========================================================================
# Batch → N pending orders
# ===========================================================================


def test_batch_signal_fires_n_pending_orders(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1002)

    today = datetime.now(UTC).date().strftime("%d.%m.%Y")
    _create_parser(
        app_client,
        headers,
        chat_id=-1002,
        parser_type="batch",
        parser_config={
            "row": (
                r"^(?P<time>\d{1,2}:\d{2})\s+"
                r"(?P<asset>\S+)\s+"
                r"(?P<direction>CALL|PUT)\s*$"
            ),
            "row_kind": "regex",
            "header": (
                r"DATE\s*:\s*(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4}).*?"
                r"TIMEZONE\s*:\s*UTC/GMT\s*\((?P<tz_offset>[+-]\d{1,2}(?::?\d{2})?)\)"
            ),
            "header_kind": "regex",
        },
        trade_mode="scheduled",
    )
    _activate(app_client, headers)

    # A four-row scheduled batch — fire times computed from the
    # header date+tz so they're in the future.
    future = datetime.now(UTC) + timedelta(hours=2)
    text = (
        f"DATE: {today}\n"
        "TIMEZONE : UTC/GMT (+00:00)\n\n"
        f"{future.strftime('%H:%M')} EURUSD CALL\n"
        f"{(future + timedelta(minutes=2)).strftime('%H:%M')} EURUSD PUT\n"
        f"{(future + timedelta(minutes=4)).strftime('%H:%M')} GBPUSD CALL\n"
        f"{(future + timedelta(minutes=6)).strftime('%H:%M')} GBPUSD PUT\n"
    )
    _run(_dispatch(app_client, chat_id=-1002, text=text))

    assert len(FakeQuotex.pending_calls) == 4
    assets = [c["asset"] for c in FakeQuotex.pending_calls]
    assert assets == ["EURUSD", "EURUSD", "GBPUSD", "GBPUSD"]

    r = app_client.get("/pipeline/trades", headers=headers)
    assert len(r.json()) == 4


# ===========================================================================
# Disabled parser → blocked
# ===========================================================================


def test_disabled_parser_blocks_dispatch(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        enabled=False,
    )
    _activate(app_client, headers)

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))

    assert FakeQuotex.buy_calls == []
    r = app_client.get("/pipeline/trades", headers=headers)
    # No row at all because the pipeline filters disabled configs out
    # before reaching the executor — there's nothing for the risk
    # gate to record.
    assert r.json() == []


# ===========================================================================
# Kill switch → blocks (via risk gate, with a logged TradeAttempt)
# ===========================================================================


def test_kill_switch_blocks_with_logged_rejection(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )
    _activate(app_client, headers)
    # Engage kill switch.
    r = app_client.post(
        "/pipeline/kill-switch",
        headers=headers,
        json={"active": True},
    )
    assert r.status_code == 200

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))

    assert FakeQuotex.buy_calls == []
    rows = app_client.get("/pipeline/trades", headers=headers).json()
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected"
    assert "kill switch" in rows[0]["error"].lower()


# ===========================================================================
# Activation requires broker + telegram (latter is checked even though
# we don't actually log into Telegram in tests; we use a manual fudge).
# ===========================================================================


def test_activate_rejected_without_broker(app_client: TestClient) -> None:
    headers = _login(app_client)
    # No broker connect.
    r = app_client.post(
        "/pipeline/activate",
        headers=headers,
        json={"active": True},
    )
    assert r.status_code == 409
    assert "broker" in r.json()["detail"].lower()


# ===========================================================================
# Status endpoint sanity
# ===========================================================================


def test_status_reports_counts(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )

    r = app_client.get("/pipeline/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is False
    assert body["broker_connected"] is True
    assert body["watched_chat_count"] == 1
    assert body["enabled_parser_count"] == 1
