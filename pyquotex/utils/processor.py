from datetime import datetime
from typing import Any

from pyquotex.utils.services import group_by_period


def get_color(candle: dict[str, Any]) -> str:
    """Determine candle color based on open and close prices."""
    if candle['open'] < candle['close']:
        return 'green'
    elif candle['open'] > candle['close']:
        return 'red'
    else:
        return 'gray'


def process_tick(
        tick: list[Any],
        interval: int,
        candles: dict[int, Any]
) -> dict[int, Any]:
    """Process a single tick into the candles dictionary."""
    symbol, timestamp, price, direction = tick
    interval_start = int(timestamp // interval * interval)

    if interval_start not in candles:
        candles[interval_start] = {
            'symbol': symbol,
            'open': price,
            'close': price,
            'high': price,
            'low': price,
            'timestamp': interval_start
        }

    candle = candles[interval_start]
    candle['close'] = price
    candle['high'] = max(candle['high'], price)
    candle['low'] = min(candle['low'], price)

    return candles


def get_last_n_candles(
        pair: str,
        candles: dict[str, dict[int, Any]],
        n: int = 3
) -> list[dict[str, Any]]:
    """Get last N candles with cached timestamp formatting."""
    if pair not in candles:
        return []

    sorted_periods = sorted(candles[pair].keys(), reverse=True)
    
    # Pre-format all timestamps instead of formatting in loop
    last_n_candles = []
    for period in sorted_periods[:n]:
        candle = candles[pair][period]
        last_n_candles.append({
            "start_time": datetime.utcfromtimestamp(period).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "open": candle["open"],
            "close": candle["close"],
            "high": candle["high"],
            "low": candle["low"],
        })

    return last_n_candles


def get_last_n_candles_batch(
        candles_dict: dict[str, dict[int, Any]],
        n: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Get last N candles for multiple pairs efficiently."""
    result = {}
    for pair, candles in candles_dict.items():
        result[pair] = get_last_n_candles(pair, {pair: candles}, n)
    return result


def process_candles(history: list[Any], period: int) -> list[dict[str, Any]]:
    """Process tick history into OHLC candles."""
    candles = []
    current_candle = {
        'open': None,
        'high': None,
        'low': None,
        'close': None,
        'start_time': None,
        'end_time': None,
        'ticks': 0
    }

    start_time = None
    for entry in history:
        if isinstance(entry, dict):
            timestamp = entry['time']
            price = entry['price']
        elif isinstance(entry, list):
            timestamp, price, _ = entry
        else:
            continue

        if start_time is None:
            start_time = timestamp - (timestamp % period)

        end_time = start_time + period
        if timestamp >= end_time:
            # Finish current candle
            if current_candle['open'] is not None:
                candles.append(current_candle)

            # Reset for next candle
            start_time = timestamp - (timestamp % period)
            end_time = start_time + period
            current_candle = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'start_time': start_time,
                'end_time': end_time,
                'ticks': 1
            }
        else:
            if current_candle['open'] is None:
                current_candle['open'] = price
                current_candle['high'] = price
                current_candle['low'] = price
                current_candle['start_time'] = start_time
                current_candle['end_time'] = end_time
            else:
                if price > current_candle['high']:
                    current_candle['high'] = price
                if price < current_candle['low']:
                    current_candle['low'] = price

            current_candle['close'] = price
            current_candle['end_time'] = end_time
            current_candle['ticks'] += 1

    # Add last candle if not empty
    if current_candle['open'] is not None:
        candles.append(current_candle)

    return candles[:-1] if candles else []


def process_candles_v2(
        history: dict[str, Any],
        asset: str,
        data: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Process and merge historical + realtime candles with deduplication."""
    if not history or not isinstance(history, dict):
        return data if data else []

    candles_data = history.get(asset, {})
    candles = candles_data.get("candles", [])[1:] if candles_data else []

    # Combine candles and realtime data
    combined = candles + (data if data else [])

    # Deduplicate by time to prevent same candle from being added 
    # multiple times
    if combined:
        candle_dict = {
            c.get('time'): c for c in combined
            if isinstance(c, dict) and 'time' in c
        }
        return list(candle_dict.values()) if candle_dict else []

    return combined


def calculate_candles(
        history: list[Any] | dict[str, Any],
        period: int
) -> list[dict[str, Any]]:
    """Calculate candles from tick history."""
    if isinstance(history, dict):
        history = history.get("history", history.get("candles", []))

    if not isinstance(history, list) or not history:
        return []

    grouped = group_by_period(history, period)
    if grouped is None:
        return []

    candles = []
    for minute, ticks in grouped.items():
        open_price = ticks[0][1]
        close_price = ticks[-1][1]
        high_price = max(tick[1] for tick in ticks)
        low_price = min(tick[1] for tick in ticks)
        num_ticks = len(ticks)
        candle = {
            'time': minute * period,
            'open': open_price,
            'close': close_price,
            'high': high_price,
            'low': low_price,
            'ticks': num_ticks
        }
        candles.append(candle)
    candles = candles[:-1]

    return candles


def merge_candles(candles_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Efficiently merge candles using dict comprehension."""
    if not candles_data:
        return []

    # Use dict to eliminate duplicates by time, then convert back to 
    # sorted list
    candle_dict = {
        c['time']: c for c in candles_data
        if isinstance(c, dict) and 'time' in c
    }
    return sorted(
        candle_dict.values(), key=lambda x: x['time']
    ) if candle_dict else []


def merge_candles_fast(
        candles_data: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ultra-fast candle merge for large datasets using dict comprehension."""
    if not candles_data:
        return []

    return sorted(
        {
            c['time']: c for c in candles_data
            if isinstance(c, dict) and 'time' in c
        }.values(),
        key=lambda x: x['time']
    ) or []


def aggregate_candle(
        tick: dict[int, Any],
        candles: dict[int, Any]
) -> dict[int, Any]:
    """Aggregate real-time ticks into candles dictionary."""
    for timestamp, data in tick.items():
        candle = candles.setdefault(timestamp, {
            'symbol': data['symbol'],
            'open': data['open'],
            'close': data['close'],
            'high': data['high'],
            'low': data['low'],
            'timestamp': timestamp
        })
        candle['close'] = data['close']
        candle['high'] = max(candle['high'], data['high'])
        candle['low'] = min(candle['low'], data['low'])

    return candles