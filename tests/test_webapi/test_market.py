"""Market-data endpoints (candles + historical-candles + sentiment)."""
from __future__ import annotations


def test_candles_endpoint(client, auth_headers, fake_quotex):
    r = client.get(
        "/market/candles?asset=EURUSD&period=60&offset=3600",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["asset"] == "EURUSD"
    assert body["period"] == 60
    assert body["count"] == 5
    assert len(body["candles"]) == 5
    fake_quotex.get_candles.assert_awaited()


def test_candles_502_on_broker_error(client, auth_headers, fake_quotex):
    fake_quotex.get_candles.side_effect = RuntimeError("dead")
    r = client.get(
        "/market/candles?asset=EURUSD&period=60&offset=60",
        headers=auth_headers,
    )
    assert r.status_code == 502


def test_historical_candles(client, auth_headers, fake_quotex):
    r = client.get(
        "/market/historical-candles"
        "?asset=EURUSD&period=60&amount_of_seconds=300&max_workers=2",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["amount_of_seconds"] == 300
    assert body["count"] == 5
    assert body["oldest_time"] is not None
    fake_quotex.get_historical_candles.assert_awaited()
    kwargs = fake_quotex.get_historical_candles.await_args.kwargs
    assert kwargs["max_workers"] == 2


def test_historical_candles_caps_max_workers_via_validation(client, auth_headers):
    # max_workers > 10 must be rejected by Pydantic before hitting the broker.
    r = client.get(
        "/market/historical-candles"
        "?asset=EURUSD&period=60&amount_of_seconds=300&max_workers=99",
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_sentiment(client, auth_headers, fake_quotex):
    r = client.get("/market/sentiment/EURUSD", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["asset"] == "EURUSD"
    assert body["bullish"] == 0.65
    assert body["bearish"] == 0.35


def test_sentiment_504_on_timeout(client, auth_headers, fake_quotex):
    fake_quotex.start_realtime_sentiment.side_effect = TimeoutError("nope")
    r = client.get("/market/sentiment/EURUSD", headers=auth_headers)
    assert r.status_code == 504
