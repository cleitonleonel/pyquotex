"""Trading endpoints — buy / pending / pending-at-price / result / cancel."""
from __future__ import annotations


def test_buy(client, auth_headers, fake_quotex):
    r = client.post(
        "/trades/buy",
        headers=auth_headers,
        json={
            "amount": 1, "asset": "EURUSD", "direction": "call",
            "duration": 60, "time_mode": "TIME",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    assert body["order_id"] == "trade-1"
    fake_quotex.buy.assert_awaited()
    kwargs = fake_quotex.buy.await_args.kwargs
    assert kwargs["amount"] == 1
    assert kwargs["asset"] == "EURUSD"


def test_buy_validation_rejects_zero_amount(client, auth_headers):
    r = client.post(
        "/trades/buy",
        headers=auth_headers,
        json={
            "amount": 0, "asset": "EURUSD", "direction": "call",
            "duration": 60,
        },
    )
    assert r.status_code == 422


def test_pending(client, auth_headers, fake_quotex):
    r = client.post(
        "/trades/pending",
        headers=auth_headers,
        json={
            "amount": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration": 60,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["pending_id"] == "pending-1"
    fake_quotex.open_pending.assert_awaited()


def test_pending_400_on_zero_duration(client, auth_headers, fake_quotex):
    fake_quotex.open_pending.side_effect = ValueError(
        "duration must be a positive integer"
    )
    r = client.post(
        "/trades/pending",
        headers=auth_headers,
        json={
            "amount": 1, "asset": "EURUSD_otc", "direction": "call",
            "duration": 1,
        },
    )
    # We pass duration=1 to clear pydantic validation; ValueError from
    # the broker layer should map to 400 Bad Request.
    assert r.status_code == 400


def test_pending_at_price(client, auth_headers, fake_quotex):
    r = client.post(
        "/trades/pending-at-price",
        headers=auth_headers,
        json={
            "amount": 1, "asset": "EURUSD_otc", "direction": "call",
            "quote": 1.0852, "period": "M1",
        },
    )
    assert r.status_code == 200
    fake_quotex.open_pending_at_price.assert_awaited()


def test_trade_result_win(client, auth_headers, fake_quotex):
    r = client.get(
        "/trades/trade-1/result?duration=60&timeout=1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "win"
    assert body["profit"] == 0.94


def test_trade_result_loss(client, auth_headers, fake_quotex):
    fake_quotex.wait_for_order_close.return_value = ("loss", -1.0)
    r = client.get(
        "/trades/trade-1/result?duration=60&timeout=1",
        headers=auth_headers,
    )
    body = r.json()
    assert body["status"] == "loss"
    assert body["profit"] == -1.0


def test_trade_result_timeout(client, auth_headers, fake_quotex):
    import asyncio

    async def slow_wait(*a, **kw):
        await asyncio.sleep(5)
        return ("win", 1.0)

    fake_quotex.wait_for_order_close.side_effect = slow_wait
    r = client.get(
        "/trades/trade-1/result?duration=60&timeout=1",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "timeout"


def test_cancel_endpoint(client, auth_headers, fake_quotex):
    r = client.delete("/trades/trade-1", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] is True
    fake_quotex.sell_option.assert_awaited_with("trade-1")
