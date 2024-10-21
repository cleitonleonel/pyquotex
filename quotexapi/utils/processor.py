from quotexapi.utils.services import group_by_period


def get_color(candle):
    if candle['open'] < candle['close']:
        return 'green'
    elif candle['open'] > candle['close']:
        return 'red'
    else:
        return 'gray'


def process_candles(history, period):
    candles = []
    current_candle = {
        'open': None,
        'high': float('-inf'),
        'low': float('inf'),
        'close': None,
        'start_time': None,
        'end_time': None,
        'ticks': 0
    }

    start_time = None
    timestamp = None
    price = 0
    for entry in history:
        if isinstance(entry, dict):
            timestamp = entry['time']
            price = entry['price']
        elif isinstance(entry, list):
            timestamp, price, _ = entry
        if start_time is None:
            start_time = timestamp - (timestamp % period)

        end_time = start_time + period
        if timestamp >= end_time:

            # Concluir a vela atual
            candles.append(current_candle)

            # Resetar para a próxima vela
            start_time = timestamp - (timestamp % period)
            current_candle = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'start_time': start_time,
                'end_time': start_time + period,
                'ticks': 1
            }

        else:
            if current_candle['open'] is None:
                current_candle['open'] = price
            current_candle['close'] = price
            current_candle['high'] = max(current_candle['high'], price)
            current_candle['low'] = min(current_candle['low'], price)
            current_candle['end_time'] = end_time
            current_candle['ticks'] += 1

    # Adicionar a última vela se não estiver vazia
    if current_candle['open'] is not None:
        candles.append(current_candle)

    return candles[:-1]


def process_candles_v2(history, asset, data):
    candles_data = history.get(asset, {})
    candles = candles_data.get("candles", [])[1:]
    candles += data
    return candles


def calculate_candles(history, period):
    grouped = group_by_period(history, period)
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


def merge_candles(candles_data):
    seen_times = set()
    merged_list = []
    for candle in candles_data:
        if isinstance(candle, dict) and candle.get('time') not in seen_times:
            seen_times.add(candle['time'])
            merged_list.append(candle)
    merged_list.sort(key=lambda x: x['time'])

    return merged_list
