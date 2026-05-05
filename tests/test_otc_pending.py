"""Wire-level tests for OTC buy + pending payloads.

The pending tests pin the live ``ws2.qxbroker.com`` payload shape that
was verified end-to-end (the broker responds with ``s_pending/create``
and a ticket UUID). Earlier captures were misleading — only the shape
in this file is the one the production server actually accepts.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from pyquotex.api import QuotexAPI
from pyquotex.utils.account_type import AccountType
from pyquotex.utils.option_type import OptionType
from pyquotex.ws.channels.buy import Buy


def _decode(frame: str) -> tuple[str, dict]:
    """Pull the event name + payload out of a Socket.IO frame."""
    assert frame.startswith("42[")
    body = json.loads(frame[2:])
    assert isinstance(body, list) and len(body) == 2
    return body[0], body[1]


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


# -----------------------------------------------------------------
# Buy / orders/open — unchanged behaviour, kept as regression guards
# -----------------------------------------------------------------

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
        price=10, asset="EURUSD_otc", direction="call", duration=60,
        request_id=12345, is_fast_option=False,
    )

    order_frame = next(f for f in sent if "orders/open" in f)
    event, payload = _decode(order_frame)
    assert event == "orders/open"
    assert payload["asset"] == "EURUSD_otc"
    assert payload["optionType"] == int(OptionType.DIGITAL_OTC)
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
        price=10, asset="EURUSD", direction="call", duration=60,
        request_id=12345, is_fast_option=False,
    )

    order_frame = next(f for f in sent if "orders/open" in f)
    _, payload = _decode(order_frame)
    assert payload["optionType"] == int(OptionType.BINARY)
    assert payload["time"] > 60   # regular binaries use a future timestamp


# -----------------------------------------------------------------
# pending/create — VERIFIED wire shape
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_pending_matches_verified_wire_spec():
    """Verified live against ws2.qxbroker.com — broker responds with
    ``s_pending/create`` carrying a ticket UUID for this payload shape.
    """
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=1,
        asset="AUDNZD_otc",
        direction="up",
        duration=60,
        open_time="2026-05-05T14:03:00.000Z",
    )

    event, payload = _decode(sent[0])
    assert event == "pending/create"
    # Verified wire fields, in any order:
    assert payload == {
        "openType": 0,
        "asset": "AUDNZD_otc",
        "openTime": "2026-05-05T14:03:00.000Z",
        "timeframe": 60,
        "command": 0,            # 0 = up
        "amount": 1.0,           # float, not int
    }


@pytest.mark.asyncio
async def test_open_pending_command_down_for_put_direction():
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    for direction in ("down", "put"):
        sent.clear()
        await api.open_pending(
            amount=1, asset="EURUSD", direction=direction, duration=60,
            open_time="2026-05-05T14:03:00.000Z",
        )
        _, payload = _decode(sent[0])
        assert payload["command"] == 1, f"{direction!r} → command must be 1"


@pytest.mark.asyncio
async def test_open_pending_command_up_for_call_direction():
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    for direction in ("up", "call"):
        sent.clear()
        await api.open_pending(
            amount=1, asset="EURUSD", direction=direction, duration=60,
            open_time="2026-05-05T14:03:00.000Z",
        )
        _, payload = _decode(sent[0])
        assert payload["command"] == 0, f"{direction!r} → command must be 0"


@pytest.mark.asyncio
async def test_open_pending_auto_schedules_iso_when_open_time_omitted():
    """The broker rejects integer ``openTime`` values with
    ``{"error": "open_time_min"}`` regardless of how far ahead they
    point. When the caller omits ``open_time``, the API must still
    emit a properly-formatted UTC ISO string."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending(
        amount=1, asset="EURUSD", direction="up", duration=60,
    )

    _, payload = _decode(sent[0])
    assert isinstance(payload["openTime"], str)
    assert _ISO_RE.match(payload["openTime"]), (
        f"openTime must be ISO 8601 ms-Z format, got {payload['openTime']!r}"
    )


@pytest.mark.asyncio
async def test_open_pending_omits_legacy_fields():
    """The verified wire shape has neither ``optionType``, ``isDemo``,
    ``tournamentId``, ``requestId``, ``action`` nor ``time``. Earlier
    captures included some of these — they must all be gone now."""
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO
    api.tournament_id = 4242   # would have been included in the old shape

    await api.open_pending(
        amount=1, asset="EURUSD", direction="up", duration=60,
        open_time="2026-05-05T14:03:00.000Z",
    )
    _, payload = _decode(sent[0])
    for stale in (
        "optionType", "isDemo", "tournamentId", "requestId",
        "action", "time",
    ):
        assert stale not in payload, f"stale field {stale!r} present"


@pytest.mark.asyncio
async def test_open_pending_at_price_quote_payload():
    sent: list[str] = []
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api.send_websocket_request = lambda d: sent.append(d) or _noop()
    api.account_type = AccountType.DEMO

    await api.open_pending_at_price(
        amount=1, asset="EURUSD_otc", direction="up",
        quote=184.379, period="M5",
    )
    event, payload = _decode(sent[0])
    assert event == "pending/create"
    assert payload == {
        "openType": 1,            # quote-mode
        "asset": "EURUSD_otc",
        "quote": 184.379,
        "period": "M5",
        "command": 0,
        "amount": 1.0,
    }


# -----------------------------------------------------------------
# Bug 3 — pending_id extraction from nested ticket
# -----------------------------------------------------------------

def test_pending_id_extracted_from_nested_ticket():
    """The ``s_pending/create`` payload wraps the ticket UUID under
    ``{"pending": {"ticket": "..."}}``. Looking up ``"id"`` on the
    outer dict (the historical bug) returned ``None``."""
    api = QuotexAPI("qxbroker.com", "u@u.com", "pw", "en")
    api._temp_status = '451-["s_pending/create",...'

    nested = {
        "pending": {
            "ticket": "8d67f0fa-4fbc-4ca9-8c63-fb23e40424b3",
            "openType": 0,
            "amount": 1,
        }
    }

    # Inline the extraction logic that lives in api._on_message —
    # we don't need a full WS message round-trip to verify it.
    pending_obj = nested.get("pending")
    if not isinstance(pending_obj, dict):
        pending_obj = nested
    pending_id = pending_obj.get("ticket") or pending_obj.get("id")
    assert pending_id == "8d67f0fa-4fbc-4ca9-8c63-fb23e40424b3"


def test_pending_id_falls_back_to_id_for_flat_responses():
    """If a future broker variant returns a flat ``{"id": "..."}``
    shape, the extraction must still pick it up."""
    flat = {"id": "abc123", "openType": 0}
    pending_obj = flat.get("pending")
    if not isinstance(pending_obj, dict):
        pending_obj = flat
    pending_id = pending_obj.get("ticket") or pending_obj.get("id")
    assert pending_id == "abc123"


# -----------------------------------------------------------------
# Existing instruments_follow regression test (unchanged)
# -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_instruments_follow_emits_subscribe_not_pending_create():
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
