"""Binary Options Bot — Educational example (async API).

Strategy: RSI crossover + SMA filter.
- BUY  (call) when RSI crosses above 30 and close > SMA
- SELL (put)  when RSI crosses below 70 and close < SMA

⚠️  This is an educational example only.
    Binary options trading carries significant risk of loss.
    Always test on a DEMO account first.
"""
import asyncio
import time

from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

# ── Settings ──────────────────────────────────────────────────────────────
SYMBOL       = "EURUSD_otc"
PERIOD       = 60        # candle duration in seconds
RISK_PERCENT = 1.0       # % of balance per trade
PAYOUT       = 0.80      # estimated broker payout (e.g. 80 %)
MAX_TRADES   = 2         # max concurrent open trades
RSI_PERIOD   = 14
SMA_PERIOD   = 50


# ── Indicators (pure Python, no numpy) ────────────────────────────────────

def calc_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """Wilder-smoothed RSI — returns only the latest value."""
    if len(closes) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_sma(closes: list[float], period: int = SMA_PERIOD) -> float:
    window = closes[-period:] if len(closes) >= period else closes
    return sum(window) / len(window)


def calc_amount(balance: float, risk_pct: float, payout: float = PAYOUT) -> float:
    risk   = balance * (risk_pct / 100)
    amount = risk / payout
    return max(round(amount, 2), 1.0)


# ── Main bot ───────────────────────────────────────────────────────────────

async def main() -> None:
    email, password = credentials()
    client = Quotex(email=email, password=password)

    check, reason = await client.connect()
    if not check:
        print(f"❌ Connection failed: {reason}")
        return

    print(f"✅ Connected  |  symbol={SYMBOL}  period={PERIOD}s")

    asset, info = await client.get_available_asset(SYMBOL, force_open=True)
    if not info or not info[2]:
        print(f"❌ Asset {SYMBOL} is closed.")
        await client.close()
        return

    await client.get_all_assets()
    open_trades: list[str] = []
    prev_rsi: float | None = None

    try:
        while True:
            # ── fetch candles ────────────────────────────────────────────
            candles = await client.get_candles(
                asset, time.time(), PERIOD * (SMA_PERIOD + 5), PERIOD
            )
            if not candles or len(candles) < SMA_PERIOD + 2:
                print("⏳ Waiting for candle data…")
                await asyncio.sleep(PERIOD)
                continue

            closes = [float(c["close"]) for c in candles]
            rsi    = calc_rsi(closes)
            sma    = calc_sma(closes)

            if rsi is None or prev_rsi is None:
                prev_rsi = rsi
                await asyncio.sleep(5)
                continue

            last_close = closes[-1]
            direction: str | None = None

            # RSI crossover signals
            if prev_rsi < 30 <= rsi and last_close > sma:
                direction = "call"
            elif prev_rsi > 70 >= rsi and last_close < sma:
                direction = "put"

            prev_rsi = rsi

            if not direction:
                print(f"  RSI={rsi:.1f}  SMA={sma:.5f}  close={last_close:.5f}  — no signal")
                await asyncio.sleep(5)
                continue

            # ── respect max concurrent trades ───────────────────────────
            if len(open_trades) >= MAX_TRADES:
                print(f"⚠️  Max open trades ({MAX_TRADES}) reached — skipping signal")
                await asyncio.sleep(5)
                continue

            # ── place trade ──────────────────────────────────────────────
            balance = await client.get_balance()
            amount  = calc_amount(balance, RISK_PERCENT)

            print(
                f"🚀 {direction.upper():4s}  amount={amount}  "
                f"balance={balance:.2f}  RSI={rsi:.1f}"
            )

            status, data = await client.buy(amount, asset, direction, PERIOD)
            if status:
                trade_id = (data or {}).get("id", "?")
                open_trades.append(str(trade_id))
                print(f"  ✅ Trade placed — id={trade_id}")

                # wait for result then remove from open_trades
                async def _track(tid: str) -> None:
                    win, profit = await client.check_win(tid, PERIOD)
                    label = "WIN 🎉" if win == "win" else "LOSS 💸"
                    print(f"  {label}  id={tid}  profit={profit:+.2f}")
                    if tid in open_trades:
                        open_trades.remove(tid)

                asyncio.create_task(_track(str(trade_id)))
            else:
                print(f"  ❌ Trade failed: {data}")

            # wait for next candle boundary
            await asyncio.sleep(PERIOD)

    except KeyboardInterrupt:
        print("\n🛑 Bot stopped.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
