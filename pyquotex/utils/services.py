import math
from collections import defaultdict
from typing import Any, Callable


def nested_dict(n: int, obj_type: Callable[[], Any]) -> defaultdict:
    """Create a nested defaultdict of depth n."""
    if n == 1:
        return defaultdict(obj_type)
    else:
        return defaultdict(lambda: nested_dict(n - 1, obj_type))


def group_by_period(
        data: list[list[Any]],
        period: int
) -> dict[int, list[list[Any]]]:
    """Group tick data by timeframe period."""
    grouped = defaultdict(list)
    for tick in data:
        timestamp = int(tick[0])
        timeframe = int(timestamp // period)
        grouped[timeframe].append(tick)
    return dict(grouped)


def truncate(f: float, n: int) -> float:
    """Truncate a float to n decimal places."""
    return math.floor(f * 10 ** n) / 10 ** n
