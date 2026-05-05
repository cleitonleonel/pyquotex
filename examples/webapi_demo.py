"""End-to-end demo of the pyquotex web API.

Assumes the server is running on http://localhost:8000 with
``PYQUOTEX_API_KEY=<your-key>`` set.

Run::

    # Terminal 1 — start the server
    PYQUOTEX_EMAIL=… PYQUOTEX_PASSWORD=… PYQUOTEX_API_KEY=demo \
        python -m pyquotex.webapi

    # Terminal 2 — run the demo
    PYQUOTEX_API_KEY=demo python -m examples.webapi_demo
"""
import asyncio
import json
import os

import httpx
import websockets

BASE = os.environ.get("PYQUOTEX_WEBAPI_URL", "http://localhost:8000")
WS_BASE = BASE.replace("http", "ws", 1)
API_KEY = os.environ.get("PYQUOTEX_API_KEY", "demo")
HEADERS = {"X-API-Key": API_KEY}


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as http:
        # 1. Health probe (no auth)
        h = await http.get("/health")
        print("health:", h.json())

        # 2. Connect (warms the broker session)
        c = await http.post("/auth/connect", headers=HEADERS)
        c.raise_for_status()
        print("connected:", c.json())

        # 3. Account state
        bal = await http.get("/account/balance", headers=HEADERS)
        print("balance:", bal.json())

        # 4. Recent candles
        cdl = await http.get(
            "/market/candles",
            headers=HEADERS,
            params={"asset": "EURUSD_otc", "period": 60, "offset": 600},
        )
        body = cdl.json()
        print(f"candles: {body['count']} returned")

        # 5. Sentiment snapshot
        s = await http.get("/market/sentiment/EURUSD_otc", headers=HEADERS)
        print("sentiment:", s.json())

        # 6. Place a $1 demo trade and long-poll for the result
        trade = await http.post(
            "/trades/buy",
            headers=HEADERS,
            json={
                "amount": 1, "asset": "EURUSD_otc",
                "direction": "call", "duration": 60,
            },
        )
        trade_body = trade.json()
        print("buy:", trade_body)

        order_id = trade_body.get("order_id")
        if order_id:
            res = await http.get(
                f"/trades/{order_id}/result",
                headers=HEADERS,
                params={"duration": 60, "timeout": 90},
            )
            print("result:", res.json())

        # 7. Live price stream (5 ticks then close)
        url = (
            f"{WS_BASE}/stream/prices"
            f"?asset=EURUSD_otc&api_key={API_KEY}"
        )
        print(f"\nconnecting to {url} (5 ticks)…")
        async with websockets.connect(url) as ws:
            for _ in range(5):
                tick = json.loads(await ws.recv())
                print(f"  tick: t={tick['time']} price={tick['price']}")


if __name__ == "__main__":
    asyncio.run(main())
