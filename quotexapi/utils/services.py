import math
from collections import defaultdict


def nested_dict(n, obj_type):
    if n == 1:
        return defaultdict(obj_type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, obj_type))


def group_by_period(data, period):
    grouped = defaultdict(list)
    for tick in data:
        timestamp = tick[0]
        timeframe = int(timestamp // period)
        grouped[timeframe].append(tick)
    return grouped


def truncate(f, n):
    return math.floor(f * 10 ** n) / 10 ** n
