# PyQuotex — Complete API Reference

> **Stable API class:** `pyquotex.stable_api.Quotex`
> All methods listed below are **public** and accessible after a successful `connect()` call unless marked otherwise.

---

## Table of Contents

1. [Initialization](#1-initialization)
2. [Connection & Authentication](#2-connection--authentication)
3. [Account Management](#3-account-management)
4. [Assets & Payouts](#4-assets--payouts)
5. [Candle & Market Data](#5-candle--market-data)
6. [Real-Time Streams](#6-real-time-streams)
7. [Trading Operations](#7-trading-operations)
8. [Trade History & Results](#8-trade-history--results)
9. [Technical Indicators](#9-technical-indicators)
10. [CLI Reference (app.py)](#10-cli-reference-apppy)
11. [Error Handling](#11-error-handling)
12. [Complete Usage Examples](#12-complete-usage-examples)

---

## 1. Initialization

```python
from pyquotex.stable_api import Quotex

client = Quotex(
    email="your@email.com",
    password="your_password",
    lang="en",                        # "pt" | "en" | "es"
    host="qxbroker.com",              # default
    root_path=".",                    # local storage root
    user_data_dir="browser",          # browser profile folder
    asset_default="EURUSD",           # default asset
    period_default=60,                # default candle period (seconds)
    proxies=None,                     # optional {"http": "...", "https": "..."}
    on_otp_callback=None,             # async callable for 2FA/OTP input
)
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `email` | `str` | required | Account email |
| `password` | `str` | required | Account password |
| `lang` | `str` | `"pt"` | Platform language (`pt`, `en`, `es`) |
| `host` | `str` | `"qxbroker.com"` | Broker hostname |
| `root_path` | `str` | `"."` | Root path for local session storage |
| `user_data_dir` | `str` | `"browser"` | Browser profile directory |
| `asset_default` | `str` | `"EURUSD"` | Default asset |
| `period_default` | `int` | `60` | Default candle period (seconds) |
| `proxies` | `dict \| None` | `None` | HTTP/HTTPS proxy settings |
| `on_otp_callback` | `callable \| None` | `None` | Async callback for OTP/2FA input |

---

## 2. Connection & Authentication

### `connect() → tuple[bool, str]`
Establishes the WebSocket connection and authenticates the session.

```python
check, reason = await client.connect()
if not check:
    print(f"Connection failed: {reason}")
```

| Returns | Description |
|---|---|
| `(True, message)` | Connected successfully |
| `(False, message)` | Connection or auth failed |

---

### `check_connect() → bool`
Returns `True` if currently connected and authenticated.

```python
if await client.check_connect():
    print("Connected!")
```

---

### `reconnect() → None`
Triggers an internal reconnect. Used automatically on disconnect.

```python
await client.reconnect()
```

---

### `close() → bool`
Gracefully closes the WebSocket connection and stops all tasks.

```python
await client.close()
```

---

### `set_session(user_agent, cookies, ssid) → None`
Manually injects a pre-existing session (bypass login).

```python
client.set_session(
    user_agent="Mozilla/5.0 ...",
    cookies="__cf_bm=...; SSID=...",
    ssid="your-ssid-token"
)
```

---

## 3. Account Management

### `get_profile() → Profile`
Returns the user profile object.

```python
profile = await client.get_profile()
print(profile.nick_name)       # display name
print(profile.demo_balance)    # demo balance (float)
print(profile.live_balance)    # live balance (float)
print(profile.currency_symbol) # e.g. "$"
print(profile.country_name)    # country
print(profile.offset)          # timezone offset (seconds)
```

---

### `get_balance(timeout=30) → float`
Returns the current balance for the active account type.

```python
balance = await client.get_balance()
print(f"Balance: {balance:.2f}")
```

---

### `change_account(balance_mode, tournament_id=0) → None`
Switches between Demo, Live, or Tournament account.

```python
from pyquotex.utils.account_type import AccountType

await client.change_account("PRACTICE")             # Demo
await client.change_account("REAL")                 # Live
await client.change_account("PRACTICE", tournament_id=1)  # Tournament
```

| `balance_mode` | Description |
|---|---|
| `"PRACTICE"` | Demo account |
| `"REAL"` | Live account |

---

### `set_account_mode(balance_mode="PRACTICE") → None`
Synchronously sets the account mode without sending a WS request.

```python
client.set_account_mode("REAL")
```

---

### `edit_practice_balance(amount=None, timeout=30) → dict`
Refills or sets the demo balance to a specific amount.

```python
result = await client.edit_practice_balance(amount=10000)
```

> ⚠️ Demo account only.

---

### `get_server_time() → int`
Returns the current synced server Unix timestamp.

```python
ts = await client.get_server_time()
from datetime import datetime
print(datetime.fromtimestamp(ts))
```

---

### `change_time_offset(time_offset) → Any`
Updates the timezone offset on the server.

```python
await client.change_time_offset(3600)  # UTC+1
```

---

### `store_settings_apply(asset, period, time_mode, deal, ...) → dict`
Applies trading-UI settings and returns the server confirmation.

```python
result = await client.store_settings_apply(
    asset="EURUSD",
    period=60,
    time_mode="TIMER",   # "TIMER" | "TURBO"
    deal=5,
    percent_mode=False,
    percent_deal=1,
)
```

---

## 4. Assets & Payouts

### `get_instruments(timeout=30) → list`
Returns the full list of trading instruments from the broker.

```python
instruments = await client.get_instruments()
# Each instrument is a list: [id, symbol, name, ..., open_status, ...]
```

---

### `get_all_assets() → dict[str, str]`
Returns a mapping of asset symbol → internal asset code.

```python
codes = await client.get_all_assets()
# {"EURUSD": "EURUSD_code", ...}
```

---

### `get_all_asset_name() → list[list[str]] | None`
Returns a list of `[code, display_name]` pairs for all assets.

```python
names = client.get_all_asset_name()
for code, name in names:
    print(code, name)
```

---

### `get_available_asset(asset_name, force_open=False) → tuple[str, Any]`
Checks if an asset is open. If `force_open=True`, tries the OTC variant.

```python
asset, info = await client.get_available_asset("EURUSD", force_open=True)
# info = (id, name, is_open)
if info[2]:
    print(f"{asset} is open for trading")
```

---

### `check_asset_open(asset_name) → tuple`
Returns raw instrument data and status for a specific asset.

```python
raw, status = await client.check_asset_open("EURUSD")
# status = (id, name, is_open)
```

---

### `get_payment() → dict`
Returns payout percentages for all assets.

```python
payouts = client.get_payment()
for asset, data in payouts.items():
    print(asset, data["payment"], data["turbo_payment"])
    # data keys: payment, turbo_payment, profit (dict), open
```

---

### `get_payout_by_asset(asset_name, timeframe="1") → float | dict | None`
Returns payout % for a specific asset.

```python
pct = client.get_payout_by_asset("EURUSD", timeframe="1")   # 1M payout %
pct = client.get_payout_by_asset("EURUSD", timeframe="5")   # 5M payout %
pct = client.get_payout_by_asset("EURUSD", timeframe="all") # all timeframes
```

---

## 5. Candle & Market Data

### `get_candles(asset, end_from_time, offset, period, ...) → list[dict] | None`
Fetches up to 199 candles ending at `end_from_time`.

```python
import time

candles = await client.get_candles(
    asset="EURUSD",
    end_from_time=time.time(),
    offset=3600,   # how far back in seconds
    period=60,     # candle size in seconds
)
# Each candle: {"time": int, "open": float, "close": float, "max": float, "min": float}
```

> ⚠️ The broker caps responses at 199 candles per request. Use `get_historical_candles` for larger ranges.

---

### `get_candle_v2(asset, period, timeout=30) → list[dict] | None`
Fetches candles via the v2 API path (merged with v1 data).

```python
candles = await client.get_candle_v2("EURUSD", period=60)
```

---

### `get_historical_candles(asset, amount_of_seconds, period, timeout=30, max_workers=5, progress_callback=None) → list[dict]`
Fetches deep historical data using parallel workers.

```python
candles = await client.get_historical_candles(
    asset="EURUSD",
    amount_of_seconds=86400,   # 24 hours
    period=60,
    max_workers=5,             # ⚠️ keep ≤ 10 to avoid bans
)
# Returns sorted list of candle dicts
```

**Progress callback signature:**
```python
def on_progress(done: int, total: int, count: int, label: str) -> None:
    print(f"{label}: {done}/{total} — {count} candles")

candles = await client.get_historical_candles(
    "EURUSD", 86400, 60, progress_callback=on_progress
)
```

---

### `get_history_line(asset, end_from_time, offset, timeout=30) → dict | None`
Fetches raw historical price-line data.

```python
data = await client.get_history_line("EURUSD", time.time(), 3600)
```

---

### `get_candles_deep(*args, **kwargs)` *(deprecated)*
Alias for `get_historical_candles`. Use `get_historical_candles` instead.

---

### `opening_closing_current_candle(asset, period=0) → dict`
Returns timing info for the currently active candle.

```python
info = await client.opening_closing_current_candle("EURUSD", 60)
# {"opening": unix_ts, "closing": unix_ts, "remaining": seconds, ...}
```

---

## 6. Real-Time Streams

### `start_candles_stream(asset, period=0) → None`
Subscribes to the candle stream for an asset.

```python
await client.start_candles_stream("EURUSD", 60)
```

---

### `stop_candles_stream(asset) → None`
Unsubscribe from the candle stream and clean up the replay state.

```python
await client.stop_candles_stream("EURUSD")
```

> v1.3.0: also drops the asset from the reconnect-replay lists (`subscribe_candle`, `subscribe_candle_all_size`) and the subscribe-once cache (`_subscribed_assets`). Long sessions that rotate through assets no longer accumulate dead entries that get re-subscribed on every reconnect.

---

### `start_realtime_price(asset, period=0, timeout=30) → dict`
Starts following live price ticks and waits for the first data.

```python
await client.start_realtime_price("EURUSD", 60)
prices = await client.get_realtime_price("EURUSD")
# [{"price": float, "time": int, ...}, ...]
```

---

### `get_realtime_price(asset) → list[dict]`
Returns the current cached real-time price list.

```python
prices = await client.get_realtime_price("EURUSD")
latest = prices[-1] if prices else None
```

---

### `start_realtime_sentiment(asset, period=0, timeout=30) → dict`
Subscribe to the trader-sentiment feed and wait for the first payload.

```python
await client.start_realtime_sentiment("EURUSD", 60)
sentiment = await client.get_realtime_sentiment("EURUSD")
# {"call": 65, "put": 35}   (percentages)
```

> v1.3.0: event-driven (waits on `sentiment_ready_<asset>`); previously polled every 200 ms. Returns the moment data lands instead of after a poll cycle. The fast path returns immediately when data is already cached.

---

### `get_realtime_sentiment(asset) → dict`
Returns current cached sentiment data.

```python
s = await client.get_realtime_sentiment("EURUSD")
print(f"CALL: {s.get('call')}%  PUT: {s.get('put')}%")
```

---

### `start_realtime_candle(asset, period=0, timeout=30) → dict`
Starts processing live candle ticks and returns the first processed result.

```python
candle = await client.start_realtime_candle("EURUSD", 60)
```

---

### `get_realtime_candles(asset) → list | dict`
Returns the current raw real-time candle tick data.

```python
ticks = await client.get_realtime_candles("EURUSD")
```

---

### `start_signals_data() → None`
Subscribes to the global trading signals stream.

```python
await client.start_signals_data()
await asyncio.sleep(2)
signals = client.get_signal_data()
```

---

### `get_signal_data() → dict`
Returns the current signal data received from the stream.

```python
signals = client.get_signal_data()
```

---

### `start_mood_stream(asset, instrument="turbo-option") → None`
Subscribes to the mood/sentiment stream for an asset.

```python
await client.start_mood_stream("EURUSD")
```

---

## 7. Trading Operations

### `buy(amount, asset, direction, duration, time_mode="TIME") → tuple[bool, Any]`
Places an immediate binary option trade.

```python
status, data = await client.buy(
    amount=10.0,
    asset="EURUSD",
    direction="call",   # "call" (UP) | "put" (DOWN)
    duration=60,        # seconds
)
if status:
    trade_id = data.get("id")
    print(f"Trade placed: {trade_id}")
```

| Parameter | Type | Description |
|---|---|---|
| `amount` | `float` | Trade amount |
| `asset` | `str` | Asset symbol (e.g. `"EURUSD"`) |
| `direction` | `str` | `"call"` or `"put"` |
| `duration` | `int` | Duration in seconds |
| `time_mode` | `str` | `"TIME"` (default) or `"TURBO"` |

---

### `open_pending(amount, asset, direction, duration, open_time=None, confirm_timeout=30.0) → tuple[bool, Any]`
Place a time-based pending order. Verified end-to-end against `ws2.qxbroker.com` in v1.2/v1.3.

```python
status, data = await client.open_pending(
    amount=1.0,
    asset="EURUSD_otc",
    direction="call",       # "call"/"put" or "up"/"down"
    duration=60,            # resulting trade duration in seconds
    open_time=None,         # None → auto-schedule to next 60s boundary ≥90s ahead
    confirm_timeout=30.0,   # how long to wait for the broker ACK (v1.2.0+)
)
```

| Param | Notes |
|---|---|
| `open_time` | `None` for auto-schedule, `"DD/MM HH:MM"` user-local, or pre-formatted ISO 8601 UTC. The wrapper auto-detects ISO and passes it straight through (v1.2.1+). Integer values are rejected by the broker with `open_time_min`. |
| `confirm_timeout` | Event-driven wait on `pending_confirmed` (v1.2.0+); default 30 s. |

> Wire shape (verified live capture): `{"openType": 0, "asset": …, "openTime": "…Z", "timeframe": …, "command": "call"|"put", "amount": …}`. See [Advanced Features → Pending Orders](12.%20Advanced%20Features#pending-orders-time-based-and-quote-triggered) for the full reference.

---

### `open_pending_at_price(amount, asset, direction, quote, period="M1", timeout=30.0) → tuple[bool, Any]`
> ⚠️ **Experimental** — only time-based mode has been verified end-to-end against the broker.

Place a quote-triggered pending order that fires when the asset's price reaches `quote`.

```python
status, data = await client.open_pending_at_price(
    amount=1, asset="EURUSD_otc", direction="call",
    quote=1.0852, period="M1",
)
```

---

### `sell_option(options_ids, timeout=30) → dict`
Sells (closes) an active option before expiration.

```python
result = await client.sell_option("trade_id_here")
# or multiple:
result = await client.sell_option(["id1", "id2"])
```

---

### `check_win(order_id, duration=0, timeout=None) → tuple[str, float]`
Block until the order settles; return `(status, profit)`. Event-driven in v1.2.0+ — wakes the moment the broker emits the close payload, instead of polling every 200 ms.

```python
win, profit = await client.check_win(trade_id, duration=60)
if win == "win":
    print(f"Won! Profit: {profit:.2f}")
else:
    print(f"Lost. Amount: {profit:.2f}")
```

| Param | Notes |
|---|---|
| `duration` | Used to size the default timeout (`duration + 30 s`). |
| `timeout` | Explicit deadline; defaults to `duration + 30` or `300` if `duration=0`. |

| Returns | Description |
|---|---|
| `("win", profit)` | Trade won |
| `("loss", profit)` | Trade lost |

Works for both immediate `buy()` orders and time-based `open_pending()` tickets — the lifecycle bridge mirrors the executed-trade close back onto the pending ticket.

---

### `wait_for_order_close(order_id, duration=0, timeout=None) → tuple[str, float]`
Public alias for `check_win` — clearer for callers who aren't guessing at win/loss. Same semantics; same return.

---

### `get_result(operation_id) → tuple[str | None, Any]`
Looks up a historical trade result by operation ID.

```python
status, detail = await client.get_result("operation-id-123")
if status:
    print(f"Result: {status}")  # "win" or "loss"
```

---

### `get_profit() → float`
Returns the profit amount from the current active operation.

```python
profit = client.get_profit()
```

---

## 8. Trade History & Results

### `get_history() → list[dict]`
Returns the recent trade history for the active account type (page 1).

```python
trades = await client.get_history()
for t in trades:
    print(t.get("asset"), t.get("profitAmount"))
```

---

### `get_trader_history(account_type, page_number) → dict`
Returns a specific page of trade history.

```python
from pyquotex.utils.account_type import AccountType

page = await client.get_trader_history(AccountType.DEMO, page_number=1)
trades = page.get("data", [])
```

| `account_type` | Value |
|---|---|
| `AccountType.DEMO` | `1` |
| `AccountType.REAL` | `0` |

---

## 9. Technical Indicators

### `calculate_indicator(asset, indicator, params=None, history_size=3600, timeframe=60) → dict`
Calculates a technical indicator from historical candle data.

```python
result = await client.calculate_indicator(
    asset="EURUSD",
    indicator="RSI",
    params={"period": 14},
    timeframe=60,
)
```

**Supported indicators:**

| Name | `params` keys | Description |
|---|---|---|
| `RSI` | `period` | Relative Strength Index |
| `MACD` | `fast_period`, `slow_period`, `signal_period` | MACD |
| `BOLLINGER` | `period`, `std_dev` | Bollinger Bands |
| `STOCHASTIC` | `k_period`, `d_period` | Stochastic Oscillator |
| `ADX` | `period` | Average Directional Index |
| `ATR` | `period` | Average True Range |
| `SMA` | `period` | Simple Moving Average |
| `EMA` | `period` | Exponential Moving Average |
| `ICHIMOKU` | `tenkan`, `kijun`, `senkou` | Ichimoku Cloud |

---

### `subscribe_indicator(asset, indicator, params=None, callback=None, timeframe=60) → None`
Subscribes to live indicator updates — callback is called on every new candle.

```python
async def on_signal(data: dict) -> None:
    print(f"RSI: {data.get('rsi')}")

await client.subscribe_indicator(
    asset="EURUSD",
    indicator="RSI",
    params={"period": 14},
    callback=on_signal,
    timeframe=60,
)
```

---

## 10. CLI Reference (app.py)

Run any command via `python app.py <command> [options]`.

### Connection & Account

| Command | Description | Key Options |
|---|---|---|
| `login` | Connect and show profile + balance | `--demo` / `--live` |
| `balance` | Show current balance | `--demo` / `--live` |
| `server-time` | Show synced server timestamp | — |
| `set-demo-balance` | Refill/set demo balance | `--amount 10000` |
| `settings` | Apply and view trading-UI settings | `--asset`, `--period`, `--mode`, `--deal` |

### Assets & Payouts

| Command | Description | Key Options |
|---|---|---|
| `assets` | List all assets with status & payout | — |
| `payout` | Show payout % for all assets | — |
| `payout-asset` | Show payout % for one asset | `--asset EURUSD`, `--timeframe 1` |

### Candle & Market Data

| Command | Description | Key Options |
|---|---|---|
| `candles` | Fetch latest candles (≤199) | `--asset`, `--period`, `--count` |
| `candles-v2` | Fetch candles via v2 API | `--asset`, `--period` |
| `candles-deep` | Fetch deep historical data | `--asset`, `--seconds`, `--workers`, `--output file.csv` |
| `history-line` | Raw historical price-line data | `--asset`, `--offset` |
| `candle-info` | Opening/closing/remaining of current candle | `--asset`, `--period` |
| `realtime-price` | Live price stream | `--asset`, `--period` |
| `realtime-sentiment` | Live trader-sentiment stream | `--asset`, `--period` |
| `realtime-candle` | Live candle tick stream | `--asset`, `--period` |

### Trading

| Command | Description | Key Options |
|---|---|---|
| `buy` | Place an immediate binary trade | `--asset`, `--amount`, `--direction call/put`, `--duration`, `--check-win` |
| `sell` | Sell/close an open position | `--id TRADE_ID` |
| `pending` | Place a pending order | `--asset`, `--amount`, `--direction`, `--duration`, `--open-time HH:MM` |
| `check` | Check trade win/loss by ID | `--id TRADE_ID` |
| `result` | Look up result from history by operation ID | `--id OPERATION_ID` |
| `signals` | Show current signal data | — |

### History

| Command | Description | Key Options |
|---|---|---|
| `history` | Show trade history | `--pages 2`, `--demo` / `--live` |

### Indicators

| Command | Description | Key Options |
|---|---|---|
| `indicator` | Calculate a technical indicator | `--asset`, `--name RSI`, `--period 14`, `--timeframe 60` |

### Monitoring & Strategy

| Command | Description | Key Options |
|---|---|---|
| `monitor` | Real-time price monitor | `--asset`, `--period` |
| `strategy` | Run Triple-Confirmation strategy | `--asset`, `--auto-trade` (DEMO only) |
| `test-all` | Smoke-test every major API method | — |

---

## 11. Error Handling

```python
import asyncio
from pyquotex.stable_api import Quotex

async def safe_example():
    client = Quotex(email="...", password="...")
    try:
        check, reason = await client.connect()
        if not check:
            print(f"Connection failed: {reason}")
            return

        try:
            balance = await client.get_balance()
        except RuntimeError as e:
            print(f"Not connected: {e}")
        except TimeoutError as e:
            print(f"Timeout: {e}")

        try:
            result = await client.edit_practice_balance(10000)
        except TimeoutError:
            print("Balance update timed out")

    finally:
        await client.close()
```

**Common exceptions:**

| Exception | When raised |
|---|---|
| `RuntimeError("API not initialized")` | Method called before `connect()` |
| `RuntimeError("Not connected to Quotex")` | `get_balance()` called while disconnected |
| `TimeoutError(...)` | WS response not received within `timeout` seconds |
| `ValueError("Callback must be provided")` | `subscribe_indicator()` called without `callback` |

---

## 12. Complete Usage Examples

### Connect and get balance

```python
import asyncio
from pyquotex.stable_api import Quotex

async def main():
    client = Quotex(email="you@example.com", password="secret")
    check, reason = await client.connect()
    if not check:
        print(f"Failed: {reason}")
        return

    profile = await client.get_profile()
    balance = await client.get_balance()
    print(f"Hello {profile.nick_name} — Balance: {balance:.2f}")
    await client.close()

asyncio.run(main())
```

---

### Fetch candles and calculate RSI

```python
async def rsi_example(client):
    asset, _ = await client.get_available_asset("EURUSD", force_open=True)

    result = await client.calculate_indicator(
        asset, "RSI", params={"period": 14}, timeframe=60
    )
    print(f"RSI: {result}")
```

---

### Place a trade and wait for result

```python
async def trade_example(client):
    asset, info = await client.get_available_asset("EURUSD", force_open=True)
    if not info[2]:
        print("Asset closed")
        return

    status, data = await client.buy(10.0, asset, "call", 60)
    if not status:
        print(f"Order failed: {data}")
        return

    trade_id = data.get("id")
    print(f"Trade placed: {trade_id}")

    win, profit = await client.check_win(trade_id, 60)
    print(f"Result: {win.upper()} — Profit: {profit:+.2f}")
```

---

### Deep history fetch with CSV export

```python
import csv
async def history_export(client):
    candles = await client.get_historical_candles(
        "EURUSD",
        amount_of_seconds=86400,  # 24h
        period=60,
        max_workers=5,
    )
    print(f"Fetched {len(candles)} candles")

    with open("eurusd_1d.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=candles[0].keys())
        writer.writeheader()
        writer.writerows(candles)
```

---

### Live indicator subscription

```python
async def live_rsi(client):
    async def on_rsi(data):
        val = data.get("rsi") or data.get("value")
        signal = "BUY 🟢" if val < 30 else "SELL 🔴" if val > 70 else "HOLD ⚪"
        print(f"RSI={val:.2f}  →  {signal}")

    await client.subscribe_indicator(
        "EURUSD", "RSI",
        params={"period": 14},
        callback=on_rsi,
        timeframe=60,
    )
```

---

### Pending order

```python
async def pending_example(client):
    status, data = await client.open_pending(
        amount=10.0,
        asset="EURUSD",
        direction="call",
        duration=60,
        open_time="15:00",
    )
    if status:
        print(f"Pending order set: {data}")
```

---

### Monitor all assets for open status

```python
async def scan_assets(client):
    await client.get_all_assets()
    instruments = await client.get_instruments()
    open_assets = [i[1] for i in instruments if i[14]]
    print(f"Open assets ({len(open_assets)}): {', '.join(open_assets[:10])}")
```

---

*Generated for PyQuotex — Unofficial Quotex Library*
*Source: [iahmedani/pyquotex](https://github.com/iahmedani/pyquotex) (fork of [cleitonleonel/pyquotex](https://github.com/cleitonleonel/pyquotex))*
