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
async def test_open_pending_otc_includes_digital_option_type():
    """The historical bug: OTC pending payload missed ``optionType``."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=10,
        asset="EURUSD_otc",
        direction="call",
        duration=60,
        open_time=1234567890,
    )

    event, payload = _decode(sent[0])
    assert event == "pending/create"
    assert payload["optionType"] == int(OptionType.DIGITAL_OTC)
    assert payload["asset"] == "EURUSD_otc"
    assert payload["openTime"] == 1234567890
    assert payload["time"] == 60


@pytest.mark.asyncio
async def test_open_pending_regular_uses_binary_option_type():
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=10,
        asset="EURUSD",
        direction="put",
        duration=300,
        open_time=1234567890,
    )

    _, payload = _decode(sent[0])
    assert payload["optionType"] == int(OptionType.BINARY)


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
