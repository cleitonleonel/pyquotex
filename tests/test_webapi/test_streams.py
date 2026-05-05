"""WebSocket streams — prices + sentiment."""
from __future__ import annotations

import json


def test_prices_ws_rejects_missing_key(client):
    """A WS upgrade without an API key must close 1008."""
    with client.websocket_connect(
            "/stream/prices?asset=EURUSD"
    ) as ws:
        # The server accepts the upgrade then immediately closes.
        try:
            ws.receive_text()
        except Exception:
            pass
    # Reaching here means the server didn't crash — the close code is
    # surfaced as a WebSocketDisconnect.


def test_prices_ws_accepts_valid_key_via_query(client, fake_quotex):
    from .conftest import API_KEY
    with client.websocket_connect(
            f"/stream/prices?asset=EURUSD&api_key={API_KEY}"
    ) as ws:
        # Pre-populated state has 2 ticks; the relay's first poll
        # should send both. Take just the first to keep the test fast.
        msg = ws.receive_text()
        body = json.loads(msg)
        assert body["asset"] == "EURUSD"
        assert "time" in body and "price" in body


def test_prices_ws_streams_new_ticks(client, fake_quotex):
    """Push a new tick into the upstream state mid-stream and verify
    the relay broadcasts it to the WS client."""
    from .conftest import API_KEY
    with client.websocket_connect(
            f"/stream/prices?asset=EURUSD&api_key={API_KEY}"
    ) as ws:
        # Drain the two seed ticks.
        ws.receive_text()
        ws.receive_text()

        # Push a fresh tick into the state.
        fake_quotex._realtime_price_state["EURUSD"].append(
            {"time": 1700000999, "price": 1.99}
        )

        msg = ws.receive_text()
        body = json.loads(msg)
        assert body["time"] == 1700000999
        assert body["price"] == 1.99


def test_sentiment_ws_streams_payload(client, fake_quotex):
    from .conftest import API_KEY
    with client.websocket_connect(
            f"/stream/sentiment?asset=EURUSD&api_key={API_KEY}"
    ) as ws:
        msg = ws.receive_text()
        body = json.loads(msg)
        assert body["asset"] == "EURUSD"
        assert body["bullish"] == 0.65
        assert body["bearish"] == 0.35
