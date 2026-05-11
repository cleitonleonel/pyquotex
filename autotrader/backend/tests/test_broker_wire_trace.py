"""Tests for ``BrokerWireTrace`` — debug-flag-gated WS forensic logger.

The tracer is a diagnostic tool that captures pyquotex's outgoing
socket.io frames around one broker call so the next ``broker_error:
TimeoutError`` leaves a wire-level record (which subscribes went out,
whether the broker ever pushed a tick, how the registry events
moved). Off by default; the wrap has to be perfectly reversible so a
``buy()`` that raises can't leak a monkey-patch into the next trade.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from autotrader.services.broker_wire_trace import BrokerWireTrace

# ---------------------------------------------------------------------------
# Fakes — minimal stand-in for ``client.api`` shape the tracer reads.
# ---------------------------------------------------------------------------


class _FakeEvent:
    """Stub of pyquotex.utils.async_utils.AsyncEvent — only the bits the
    tracer reads.
    """

    def __init__(self, fired: bool = False) -> None:
        self._has_fired = fired


def _build_fake_api(
    *,
    subscribed_assets: set[str] | None = None,
    realtime_price: dict[str, list[dict[str, Any]]] | None = None,
    fired_events: set[str] | None = None,
    current_asset: str | None = "EURUSD",
) -> Any:
    """Build a pyquotex-shaped client + api pair.

    Only the attributes the tracer touches are populated. Frames sent
    through ``send_websocket_request`` are appended to ``api.sent``.
    """
    api = MagicMock()
    api.sent = []

    async def _send(data: str) -> None:
        api.sent.append(data)

    api.send_websocket_request = _send
    api.realtime_price = realtime_price or {}
    api.current_asset = current_asset
    api.pending_id = None
    api.state.status = "CONNECTED"

    fired = fired_events or set()
    api.event_registry._events = {key: _FakeEvent(fired=True) for key in fired}

    client = MagicMock()
    client.api = api
    client._subscribed_assets = subscribed_assets or set()
    return client


# ---------------------------------------------------------------------------
# Disabled trace = absolute no-op (no wrap, no logs, no allocations on
# the hot path)
# ---------------------------------------------------------------------------


async def test_disabled_trace_does_not_wrap_send() -> None:
    client = _build_fake_api()
    original_send = client.api.send_websocket_request

    async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=False):
        # Body runs unchanged. Crucially the send fn is *exactly* the
        # one we started with — no wrapper installed.
        assert client.api.send_websocket_request is original_send
        await client.api.send_websocket_request('42["instruments/update", {}]')

    # And it's still untouched after exit.
    assert client.api.send_websocket_request is original_send
    # Frames flowed through to the underlying fake.
    assert client.api.sent == ['42["instruments/update", {}]']


async def test_disabled_trace_emits_no_logs() -> None:
    """When the flag is off we must not pay structured-log cost — the
    operator turned the diagnostic off, so we stay silent.
    """
    client = _build_fake_api()

    with capture_logs() as logs:
        async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=False):
            pass

    wire_events = [
        entry for entry in logs
        if "broker_wire" in (entry.get("event") or "")
    ]
    assert wire_events == []


# ---------------------------------------------------------------------------
# Enabled trace captures every outgoing frame
# ---------------------------------------------------------------------------


async def test_enabled_trace_captures_outgoing_frames() -> None:
    client = _build_fake_api()

    async with BrokerWireTrace.if_enabled(
        client, "USDBRL_otc", enabled=True,
    ) as trace:
        # Three frames go out — exactly what start_candles_stream
        # would issue for a fresh asset.
        await client.api.send_websocket_request(
            '42["instruments/update", {"asset":"USDBRL_otc","period":60}]',
        )
        await client.api.send_websocket_request(
            '42["chart_notification/get", {"asset":"USDBRL_otc"}]',
        )
        await client.api.send_websocket_request(
            '42["depth/follow", "USDBRL_otc"]',
        )

    # All three frames captured, in order.
    payloads = [data for _, data in trace.frames]
    assert payloads == [
        '42["instruments/update", {"asset":"USDBRL_otc","period":60}]',
        '42["chart_notification/get", {"asset":"USDBRL_otc"}]',
        '42["depth/follow", "USDBRL_otc"]',
    ]
    # Timestamps are monotonic non-negative offsets.
    timestamps = [t for t, _ in trace.frames]
    assert all(t >= 0.0 for t in timestamps)
    assert timestamps == sorted(timestamps)
    # The underlying client also saw them — the wrap must be transparent.
    assert client.api.sent == payloads


async def test_enabled_trace_restores_send_on_normal_exit() -> None:
    client = _build_fake_api()
    original_send = client.api.send_websocket_request

    async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=True):
        # Inside the context the send is wrapped — definitely not the
        # original.
        assert client.api.send_websocket_request is not original_send

    # On exit, the original must be re-installed. This is the property
    # that protects every *subsequent* trade from a leaked wrapper.
    assert client.api.send_websocket_request is original_send


async def test_enabled_trace_restores_send_on_exception() -> None:
    """A buy() raising halfway through must not leave a leaked
    monkey-patch behind. Same guarantee on the failure path as on
    the success path.
    """
    client = _build_fake_api()
    original_send = client.api.send_websocket_request

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=True):
            await client.api.send_websocket_request('42["test"]')
            raise _BoomError("simulated buy() failure")

    assert client.api.send_websocket_request is original_send


# ---------------------------------------------------------------------------
# Preflight + postmortem structured logs
# ---------------------------------------------------------------------------


async def test_enabled_trace_emits_preflight_and_postmortem_logs() -> None:
    client = _build_fake_api(
        subscribed_assets={"EURUSD,60"},
        realtime_price={"EURUSD": [{"time": 1, "price": 1.1}]},
        fired_events={"price_ready_EURUSD"},
    )

    with capture_logs() as logs:
        async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=True):
            await client.api.send_websocket_request('42["test"]')

    events = [entry.get("event") for entry in logs]
    assert "executor.broker_wire.preflight" in events, logs
    assert "executor.broker_wire.postmortem" in events, logs

    # Pre-flight snapshot reflects the seeded fake state — the test
    # primes ``EURUSD`` as already subscribed with one cached tick and
    # the readiness event already fired.  The whole point of the
    # snapshot is that we can distinguish "fresh subscribe" from
    # "idempotency-cache short-circuit" on a real failure.
    preflight = next(
        entry for entry in logs
        if entry.get("event") == "executor.broker_wire.preflight"
    )
    assert preflight["asset"] == "EURUSD"
    assert preflight["asset_subscribed"] is True
    assert preflight["realtime_price_len"] == 1
    assert preflight["price_ready_fired"] is True

    postmortem = next(
        entry for entry in logs
        if entry.get("event") == "executor.broker_wire.postmortem"
    )
    assert postmortem["outcome"] == "ok"
    assert postmortem["frame_count"] == 1
    assert postmortem["frames"][0][1] == '42["test"]'
    assert postmortem["exception"] is None


async def test_postmortem_captures_timeout_outcome() -> None:
    """The whole point of the tracer is that a ``TimeoutError`` raised
    inside the wrapped block is recorded in the postmortem log — so
    when the next broker_error fires, the wire trace is right there
    next to it.
    """
    client = _build_fake_api()

    with capture_logs() as logs, pytest.raises(TimeoutError):
        async with BrokerWireTrace.if_enabled(
            client, "USDBRL_otc", enabled=True,
        ):
            await client.api.send_websocket_request(
                '42["instruments/update", {"asset":"USDBRL_otc","period":60}]',
            )
            # Simulate the 30-s ``start_realtime_price`` timeout.
            raise TimeoutError(
                "Timeout waiting for realtime price data for USDBRL_otc.",
            )

    postmortem = next(
        entry for entry in logs
        if entry.get("event") == "executor.broker_wire.postmortem"
    )
    assert postmortem["outcome"] == "error"
    assert "TimeoutError" in postmortem["exception"]
    assert "USDBRL_otc" in postmortem["exception"]
    assert postmortem["asset"] == "USDBRL_otc"
    assert postmortem["frame_count"] == 1


# ---------------------------------------------------------------------------
# Defensive: tracer must survive odd client shapes
# ---------------------------------------------------------------------------


async def test_trace_no_ops_when_client_is_none() -> None:
    """The tracer is called from the executor right before a buy().
    If for any reason the broker has dropped the client between
    submission and dispatch (race), the tracer must not crash —
    just no-op.
    """
    async with BrokerWireTrace.if_enabled(None, "EURUSD", enabled=True):
        pass  # nothing to assert; the line above must not raise


async def test_trace_no_ops_when_api_is_none() -> None:
    client = MagicMock()
    client.api = None
    async with BrokerWireTrace.if_enabled(client, "EURUSD", enabled=True):
        pass


# Run all tests in this module in asyncio mode automatically.
# ``pyproject.toml`` sets ``asyncio_mode = "auto"`` so ``async def``
# tests are picked up without an explicit marker — this line is
# defensive in case the module is run with a different config.
pytestmark = pytest.mark.asyncio
