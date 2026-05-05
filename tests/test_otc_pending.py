"""Wire-level tests for OTC buy + pending payloads.

These tests stub out the WebSocket layer and capture the raw Socket.IO
frames emitted by ``Buy.__call__`` and ``QuotexAPI.open_pending`` so we
can assert on the exact JSON payload — including the ``optionType``
field that used to be missing from pending orders for OTC pairs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.utils.account_type import AccountType
from pyquotex.utils.option_type import OptionType
from pyquotex.ws.channels.buy import Buy


def _decode(frame: str) -> tuple[str, dict]:
    """Pull the event name + payload out of a Socket.IO frame string."""
    assert frame.startswith("42[")
    body = json.loads(frame[2:])
    assert isinstance(body, list) and len(body) == 2
    return body[0], body[1]


@pytest.mark.asyncio
async def test_buy_otc_non_fast_emits_digital_payload():
    sent: list[str] = []

    api = SimpleNamespace(
        account_type=AccountType.DEMO,
        tournament_id=0,
        send_websocket_request=lambda d: sent.append(d) or _noop(),
        settings_apply=lambda *a, **kw: _noop(),
    )

    buy = Buy(api)
    await buy(
        price=10,
        asset="EURUSD_otc",
        direction="call",
        duration=60,
        request_id=12345,
        is_fast_option=False,
    )

    # Filter out the leading "tick" heartbeat frame.
    order_frame = next(f for f in sent if "orders/open" in f)
    event, payload = _decode(order_frame)

    assert event == "orders/open"
    assert payload["asset"] == "EURUSD_otc"
    assert payload["optionType"] == int(OptionType.DIGITAL_OTC)
    # OTC digital uses the duration directly as the time field.
    assert payload["time"] == 60


@pytest.mark.asyncio
async def test_buy_regular_binary_emits_binary_payload():
    sent: list[str] = []

    api = SimpleNamespace(
        account_type=AccountType.DEMO,
        tournament_id=0,
        send_websocket_request=lambda d: sent.append(d) or _noop(),
        settings_apply=lambda *a, **kw: _noop(),
    )

    buy = Buy(api)
    await buy(
        price=10,
        asset="EURUSD",
        direction="call",
        duration=60,
        request_id=12345,
        is_fast_option=False,
    )

    order_frame = next(f for f in sent if "orders/open" in f)
    _, payload = _decode(order_frame)

    assert payload["optionType"] == int(OptionType.BINARY)
    # Regular binaries use a future timestamp (well above the duration).
    assert payload["time"] > 60


@pytest.mark.asyncio
async def test_open_pending_otc_matches_wire_capture():
    """Wire shape captured from the live Quotex web client::

        {asset, amount, time, action, isDemo:true, tournamentId:null,
         requestId}

    Notably absent: ``optionType`` (broker rejects pending orders that
    include it) and ``openTime`` (omitted unless the caller explicitly
    schedules a future time).
    """
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=1,
        asset="EURUSD_otc",
        direction="up",
        duration=60,
    )

    event, payload = _decode(sent[0])
    assert event == "pending/create"
    assert payload["asset"] == "EURUSD_otc"
    assert payload["amount"] == 1
    assert payload["time"] == 60
    assert payload["action"] == "up"
    assert payload["isDemo"] is True              # bool, NOT int
    assert payload["tournamentId"] is None        # JSON null, NOT 0
    assert "requestId" in payload
    # The two fields whose presence used to break OTC pending:
    assert "optionType" not in payload
    assert "openTime" not in payload


@pytest.mark.asyncio
async def test_open_pending_real_account_emits_false_isDemo():
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.REAL

    await api.open_pending(
        amount=10, asset="EURUSD", direction="down", duration=60,
    )
    _, payload = _decode(sent[0])
    assert payload["isDemo"] is False


@pytest.mark.asyncio
async def test_open_pending_includes_open_time_when_provided():
    """If the caller passes ``open_time`` for explicit scheduling, the
    payload includes it. Default omits it (matching the wire capture)."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=1, asset="EURUSD", direction="up", duration=60,
        open_time="2025-01-01T12:00:00.000Z",
    )
    _, payload = _decode(sent[0])
    assert payload["openTime"] == "2025-01-01T12:00:00.000Z"


@pytest.mark.asyncio
async def test_open_pending_tournament_passthrough():
    """When tournament_id is non-zero, it appears verbatim in the payload."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO
    api.tournament_id = 4242

    await api.open_pending(
        amount=1, asset="EURUSD", direction="up", duration=60,
    )
    _, payload = _decode(sent[0])
    assert payload["tournamentId"] == 4242


@pytest.mark.asyncio
async def test_open_pending_at_price_quote_payload():
    """Quote-triggered pending payload from the wire capture::

        {asset, amount, quote, period, action}
    """
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending_at_price(
        amount=1,
        asset="EURUSD_otc",
        direction="up",
        quote=184.379,
        period="M5",
    )

    event, payload = _decode(sent[0])
    assert event == "pending/create"
    assert payload["asset"] == "EURUSD_otc"
    assert payload["amount"] == 1
    assert payload["quote"] == 184.379
    assert payload["period"] == "M5"
    assert payload["action"] == "up"
    assert payload["isDemo"] is True
    assert payload["tournamentId"] is None
    # No time, no openTime — quote-mode triggers on price, not duration.
    assert "time" not in payload
    assert "openTime" not in payload


@pytest.mark.asyncio
async def test_instruments_follow_emits_subscribe_not_pending_create():
    """Regression: ``instruments_follow`` used to duplicate the pending
    order. It must now send the real ``instruments/follow`` event."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()

    await api.instruments_follow("EURUSD_otc")

    assert len(sent) == 1
    event, payload = _decode(sent[0])
    assert event == "instruments/follow"
    assert payload == {"asset": "EURUSD_otc"}


async def _noop() -> None:  # pragma: no cover - trivial
    return None
