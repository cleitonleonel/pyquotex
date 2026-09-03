"""DEMO-account smoke test for the resilience + perf PR.

Exercises everything that cannot be verified offline:

  1. Async context manager (`async with Quotex(...)`).
  2. Auth on DEMO and balance retrieval.
  3. `get_candles` with `use_cache=True` (hit on second call).
  4. Streaming indicators warmed up from real candles.
  5. Subscription tracking after `start_candles_stream`.
  6. Manual auto-reconnect: close the underlying socket from underneath
     the client and confirm it comes back up + replays the candle
     subscription.

Run:
    PYTHONPATH=. python scripts/smoke_demo.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

from pyquotex import Candle, ReconnectPolicy
from pyquotex.config import credentials
from pyquotex.stable_api import Quotex
from pyquotex.utils.streaming_indicators import StreamingRSI, StreamingSMA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smoke")


SEPARATOR = "─" * 60


def banner(title: str) -> None:
    print(f"\n{SEPARATOR}\n  {title}\n{SEPARATOR}")


async def step_balance(q: Quotex) -> None:
    banner("Step 1 — balance + profile (event-driven path)")
    profile = await q.get_profile()
    balance = await q.get_balance()
    print(f"  Nick: {profile.nick_name}  Country: {profile.country_name}")
    print(f"  Currency: {profile.currency_code}  Balance(DEMO): {balance}")


async def step_candles_cache(q: Quotex) -> None:
    banner("Step 2 — get_candles with use_cache=True")
    asset = "EURUSD_otc"
    period = 60

    t0 = time.monotonic()
    first = await q.get_candles(asset, None, 3600, period, use_cache=True)
    t1 = time.monotonic() - t0
    n_first = len(first) if first else 0
    print(f"  First call: {n_first} candles in {t1*1000:.1f} ms")

    t0 = time.monotonic()
    second = await q.get_candles(asset, None, 3600, period, use_cache=True)
    t2 = time.monotonic() - t0
    n_second = len(second) if second else 0
    print(f"  Cached call: {n_second} candles in {t2*1000:.1f} ms")
    if t2 < t1 * 0.5 or t2 < 0.005:
        print("  ✅ cache hit confirmed (second call is much faster)")
    else:
        print("  ⚠️  expected speedup not observed — TTL may have expired")

    return first, asset, period  # type: ignore[return-value]


async def step_streaming_indicators(candles: list[dict[str, Any]]) -> None:
    banner("Step 3 — streaming indicators on live candles")
    if not candles:
        print("  ⚠️  no candles to feed — skipping")
        return

    sma14 = StreamingSMA(period=14)
    rsi14 = StreamingRSI(period=14)
    last_sma: float | None = None
    last_rsi: float | None = None
    for c in candles:
        close = float(c["close"])
        last_sma = sma14.update(close) or last_sma
        last_rsi = rsi14.update(close) or last_rsi

    closes = [float(c["close"]) for c in candles]
    print(f"  Candles fed: {len(closes)}")
    print(f"  SMA(14) latest: {last_sma}")
    print(f"  RSI(14) latest: {last_rsi}")
    # Sanity check against batch
    from pyquotex.utils.indicators import TechnicalIndicators
    batch = TechnicalIndicators.calculate_sma(closes, 14)
    batch_last = batch[-1] if batch else None
    print(f"  SMA(14) batch:  {batch_last}  (rounded match: "
          f"{round(last_sma or 0, 2) == round(batch_last or 0, 2)})")


async def step_typed_candle(candles: list[dict[str, Any]]) -> None:
    banner("Step 4 — Candle.from_dict typed conversion")
    if not candles:
        return
    typed = [Candle.from_dict(c) for c in candles[-3:]]
    for c in typed:
        print(f"  t={c.time} o={c.open} h={c.high} l={c.low} c={c.close} "
              f"color={c.color}")


async def step_subscription_replay(q: Quotex, asset: str, period: int) -> None:
    banner("Step 5 — subscription tracking & forced reconnect")

    # Make sure the subscription is registered.
    await q.start_candles_stream(asset, period)
    subs = q.api._subscriptions  # noqa: SLF001 — smoke test
    print(f"  Subscriptions tracked: {list(subs.keys())}")
    assert any(s.startswith("candle:" + asset) for s in subs), \
        "candle subscription not tracked"

    # Force a reconnect by closing the underlying socket directly.
    ws_client = q.api.websocket_client
    print("  Forcing socket close to trigger auto-reconnect…")
    raw_ws = ws_client._ws  # noqa: SLF001
    if raw_ws is not None:
        await raw_ws.close(code=4001, reason="smoke-test-forced")

    # Wait up to 20s for the reconnect loop to bring it back.
    for i in range(40):
        await asyncio.sleep(0.5)
        if ws_client.is_alive():
            print(f"  ✅ Reconnected after ~{(i + 1) * 0.5:.1f}s "
                  f"(open_count={ws_client._open_count})")
            break
    else:
        print("  ❌ Did not reconnect within 20s")
        return

    # Confirm subscription is still tracked (replay does NOT clear it).
    subs_after = q.api._subscriptions  # noqa: SLF001
    if any(s.startswith("candle:" + asset) for s in subs_after):
        print(f"  ✅ Subscription still tracked post-reconnect: "
              f"{list(subs_after.keys())}")

    # Confirm fresh candles flow.
    fresh = await q.get_candles(asset, None, 600, period)
    print(f"  Post-reconnect candles fetched: {len(fresh or [])}")


async def step_buy_and_check_win(q: Quotex) -> None:
    """Place a real DEMO buy and resolve it via the event-driven check_win.

    Exercises the end-to-end trade path against the broker:
      * settings_apply / orders/open frames
      * slots.buy_confirm wakes up when broker echoes the order id
      * slots.win_result(order_id) wakes up when the deal closes
    """
    banner("Step 7 — buy + check_win (DEMO trade)")
    # Try a few candidates; first one the broker accepts wins.
    candidates = ["EURUSD_otc", "EURUSD", "AUDCAD_otc", "AUDCAD"]
    amount = 1.0
    duration = 60

    sym = None
    buy_info: Any = None
    ok = False
    for candidate in candidates:
        print(f"  Trying buy on {candidate}…")
        try:
            ok, buy_info = await asyncio.wait_for(
                q.buy(amount, candidate, "call", duration),
                timeout=45,
            )
        except (TimeoutError, asyncio.TimeoutError):
            print("    realtime stream timed out — try next")
            continue
        except Exception as e:
            print(f"    exception: {e!r}")
            continue
        if ok and isinstance(buy_info, dict) and buy_info.get("id"):
            sym = candidate
            break
        print(f"    rejected: ok={ok!r} info={buy_info!r}")
    if not ok or not sym:
        print("  ⚠️  No tradeable asset right now; skipping trade step")
        return
    if not ok:
        print(f"  ❌ Buy failed: {buy_info}")
        return
    order_id = buy_info.get("id") if isinstance(buy_info, dict) else None
    print(f"  ✅ Buy accepted: order_id={order_id}")
    if not order_id:
        print("  ⚠️  No order_id returned — can't check_win")
        return

    print(f"  Waiting for trade to close (~{duration}s)…")
    win, profit = await q.check_win(order_id, duration=duration)
    print(f"  ✅ check_win → status={win!r} profit={profit}")


async def main() -> None:
    email, password = credentials()
    policy = ReconnectPolicy(
        enabled=True,
        max_attempts=0,
        base_delay=0.5,
        max_delay=10.0,
        jitter=0.1,
        stale_timeout=90.0,
    )

    log.info("Connecting as %s with auto-reconnect…", email)
    real_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:127.0) "
        "Gecko/20100101 Firefox/127.0"
    )
    async with Quotex(
        email=email,
        password=password,
        lang="en",
        user_agent=real_ua,
        reconnect_policy=policy,
    ) as q:
        q.set_account_mode("PRACTICE")
        # Re-issue change_account on the WS so the session is on DEMO.
        if q.api is not None:
            from pyquotex.utils.account_type import AccountType
            await q.api.change_account(AccountType.DEMO)
            await asyncio.sleep(0.5)

        await step_balance(q)
        candles, asset, period = await step_candles_cache(q)
        await step_streaming_indicators(candles)
        await step_typed_candle(candles)
        # Buy BEFORE the forced reconnect — it needs realtime price ticks
        # flowing for the asset, which is most reliable on a fresh socket.
        await step_buy_and_check_win(q)
        await step_subscription_replay(q, asset, period)

    banner("Done — context manager cleanly closed the connection.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
