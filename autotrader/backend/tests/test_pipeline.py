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
# Sibling parsers on the same chat each fire independently
# ===========================================================================


def test_sibling_parsers_each_fire_on_match(app_client: TestClient) -> None:
    """Two enabled parsers on one chat must both place trades on a
    matching message. Priority orders the walk; it doesn't gate which
    parsers run.
    """
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        name="primary",
        priority=10,
        default_stake=5.0,
    )
    _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        name="secondary",
        priority=20,
        default_stake=7.0,
    )
    _activate(app_client, headers)

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))

    assert len(FakeQuotex.buy_calls) == 2
    stakes = sorted(call["amount"] for call in FakeQuotex.buy_calls)
    assert stakes == [5.0, 7.0]

    r = app_client.get("/pipeline/trades", headers=headers)
    assert len(r.json()) == 2


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
    # The broker's wire format is the strict ISO-8601 UTC form
    # ``YYYY-MM-DDTHH:MM:SS.000Z`` — verified against ws2.qxbroker.com.
    # Python's ``isoformat`` ``+00:00`` representation looks
    # equivalent but in the wild the broker silently treats it as
    # broker-local time, drifting the schedule by the broker's
    # default offset. Pin the wire format so a regression to
    # ``isoformat()`` is caught.
    assert isinstance(call["open_time"], str)
    import re  # noqa: PLC0415

    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z$",
        call["open_time"],
    ), f"unexpected wire format: {call['open_time']!r}"
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
    # Telegram-pulse / subscription-health gauges added in Phase 7 of
    # the dashboard. Both are nullable / zero on a fresh app where no
    # message has flowed yet — what we're pinning here is the *shape*
    # so a regression that drops the fields is caught immediately.
    assert "last_message_received_at" in body
    assert body["last_message_received_at"] is None
    assert "subscribed_chat_count" in body
    assert body["subscribed_chat_count"] == 0


def test_decisions_endpoint_records_dispatch_outcomes(
    app_client: TestClient,
) -> None:
    """Every dispatch decision lands in the in-memory ring buffer the
    ``/pipeline/decisions`` endpoint serves. The dashboard's "Recent
    parsing decisions" panel is the only consumer today; this test
    pins the contract so a refactor of ``Pipeline._record_decision``
    doesn't silently empty the panel."""
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

    # Add a *second* watched chat with no parser bound — the
    # "watched but no parsers" surface the panel exists to flag.
    _add_watch(app_client, headers, -1002)

    # Two dispatches against the parsered watch: one matches, one doesn't.
    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))
    _run(_dispatch(app_client, chat_id=-1001, text="this is not a signal"))
    # One against the empty watched chat → ``no_configs`` surfaces.
    _run(_dispatch(app_client, chat_id=-1002, text="parser-less message"))
    # And one against a chat that's NOT watched at all — must be
    # filtered upstream so noise from random DMs / unrelated groups
    # never lands in the decisions panel.
    _run(_dispatch(app_client, chat_id=-9999, text="unwatched noise"))

    r = app_client.get("/pipeline/decisions", headers=headers)
    assert r.status_code == 200
    decisions = r.json()
    assert len(decisions) >= 3, decisions
    outcomes = [d["outcome"] for d in decisions]
    # Newest-first: the empty-watched-chat orphan was last to record.
    assert outcomes[0] == "no_configs"
    assert "matched" in outcomes
    assert "no_match" in outcomes
    # The unwatched-chat dispatch must NOT appear — pipeline filters
    # at the chat level and silently drops non-watched messages.
    chat_ids_seen = {d["chat_id"] for d in decisions}
    assert -9999 not in chat_ids_seen, (
        f"non-watched chat -9999 leaked into decisions: {chat_ids_seen}"
    )
    # Each row carries the structured shape the UI renders against.
    sample = decisions[0]
    for key in (
        "ts", "chat_id", "parser_config_id", "parser_name",
        "parser_type", "outcome", "reasons", "signals", "text_preview",
    ):
        assert key in sample, f"missing {key} in {sample}"


# ===========================================================================
# Pipeline parser-cache invalidation on config update / delete
# ===========================================================================


def test_parser_update_invalidates_pipeline_cache(app_client: TestClient) -> None:
    """Editing a parser config drops the cached parser instance.

    Stateful parsers (PrepTriggerParser, the concat Aggregator) hold
    per-chat buffers. Without invalidation, those buffers would keep
    firing on the *new* config's shape — and the implicit
    signature-drift refresh inside ``Pipeline._get_or_build`` won't
    fire the moment the user only changed the priority or the name.
    Wire it explicitly so an update is unambiguous.
    """
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    config_id = _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )
    _activate(app_client, headers)

    # Warm the cache via a dispatch.
    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))
    pipeline = app_client.app.state.pipeline  # type: ignore[attr-defined]
    assert config_id in pipeline._parsers
    # Capture the cached instance's identity. The PUT below changes only
    # ``name`` and ``priority`` — neither is in ``_config_signature``, so
    # ``_get_or_build`` would refresh ``config_row`` on the *same*
    # instance if invalidate() were skipped. A different ``id()`` after
    # the PUT therefore proves invalidate() really ran.
    cached_before = pipeline._parsers[config_id]

    # Update without changing the parser shape — the signature would
    # not drift on its own, so the cache must be flushed by the route.
    r = app_client.put(
        f"/parsers/configs/{config_id}",
        headers=headers,
        json={
            "name": "renamed",
            "priority": 50,
            "parser_type": "template",
            "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
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
        },
    )
    assert r.status_code == 200, r.text
    # invalidate() drops the stale cached instance; prebuild() (added in
    # Task 7) immediately re-inserts a fresh one because enabled=True.
    # The invariant is that the cache holds the *new* config shape — not
    # that the slot is empty. Verify (a) the rebuilt row carries the new
    # name, and (b) the cached instance is a *different* object than
    # before — without invalidate(), _get_or_build would have returned
    # the same instance with config_row simply re-pointed.
    assert config_id in pipeline._parsers
    assert pipeline._parsers[config_id].config_row.name == "renamed"
    assert pipeline._parsers[config_id] is not cached_before, (
        "PUT must invalidate the cached parser instance, not just "
        "refresh config_row in place"
    )


def test_parser_delete_invalidates_pipeline_cache(app_client: TestClient) -> None:
    headers = _login(app_client)
    _connect_broker(app_client, headers)
    _add_watch(app_client, headers, -1001)
    config_id = _create_parser(
        app_client,
        headers,
        chat_id=-1001,
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )
    _activate(app_client, headers)

    _run(_dispatch(app_client, chat_id=-1001, text="BUY EURUSD 1m"))
    pipeline = app_client.app.state.pipeline  # type: ignore[attr-defined]
    assert config_id in pipeline._parsers

    r = app_client.delete(f"/parsers/configs/{config_id}", headers=headers)
    assert r.status_code == 200
    assert config_id not in pipeline._parsers


def test_pipeline_invalidate_for_chat_drops_only_matching_caches() -> None:
    """``invalidate_for_chat(X)`` removes every cached parser whose
    ``config_row.chat_id == X``, leaving caches for other chats
    intact. Called from the unwatch endpoint so cached parsers for
    a deleted watch row don't sit in memory until a signature drift
    rebuilds them.
    """
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.services.parsers import build_parser  # noqa: PLC0415
    from autotrader.services.pipeline import Pipeline, _CachedParser  # noqa: PLC0415

    class _StubManager:
        assets: tuple[str, ...] = ()

    class _StubExecutor:
        async def submit(self, **kwargs: object) -> None:
            return None

    pipe = Pipeline(manager=_StubManager(), executor=_StubExecutor())

    def _seed(cfg_id: int, chat_id: int) -> None:
        cfg = ParserConfig(
            id=cfg_id,
            chat_id=chat_id,
            parser_type="template",
            parser_config_json='{"template": "{DIRECTION} {ASSET} {DURATION}"}',
        )
        parser = build_parser(
            parser_type="template",
            parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
        )
        pipe._parsers[cfg_id] = _CachedParser(
            config_revision=("template", "{}", "0", "{}", "60", "0"),
            parser_type="template",
            parser=parser,
            aggregator=None,
            config_row=cfg,
        )

    _seed(cfg_id=10, chat_id=-1001)
    _seed(cfg_id=11, chat_id=-1001)
    _seed(cfg_id=20, chat_id=-1002)

    pipe.invalidate_for_chat(-1001)

    assert 10 not in pipe._parsers
    assert 11 not in pipe._parsers
    assert 20 in pipe._parsers, (
        "invalidate_for_chat must only drop caches matching the chat_id; "
        "parsers on other chats stay cached"
    )
