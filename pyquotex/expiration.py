import time
import calendar
from datetime import (
    datetime,
    timedelta
)


def get_timestamp():
    return calendar.timegm(time.gmtime())


def date_to_timestamp(dt):
    return time.mktime(dt.timetuple())


def timestamp_to_date(timestamp):
    return datetime.fromtimestamp(timestamp)


def get_timestamp_days_ago(days):
    current_time = int(time.time())
    seconds_in_day = 86400
    timestamp_days_ago = current_time - (days * seconds_in_day)
    return timestamp_days_ago


def get_expiration_time_quotex(timestamp: int, duration: int) -> int:
    """
    Calculates the correct expiration timestamp for Quotex orders.

    Handles both short durations (<60s, assumed market orders) and
    scheduled future expirations (60s and above).

    Args:
        timestamp (int): Current UNIX timestamp in seconds.
        duration (int): Desired duration in seconds (must be supported by Quotex).

    Returns:
        int: Expiration UNIX timestamp.
    """
    now = datetime.fromtimestamp(timestamp)

    # For durations < 60s (only valid for market orders or OTC)
    if duration < 60:
        shift = 1 if now.second >= 30 else 0
        exp_time = now.replace(second=0, microsecond=0) + timedelta(minutes=shift + 1)
        return int(date_to_timestamp(exp_time))

    # For durations >= 60s (valid for buy_pending and scheduled trades)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_since_midnight = int((now - midnight).total_seconds())

    remainder = seconds_since_midnight % duration
    step = 2 if remainder > (duration / 2) else 1
    next_valid = ((seconds_since_midnight // duration) + step) * duration

    expiration_time = midnight + timedelta(seconds=next_valid)
    return int(expiration_time.timestamp())


def get_next_timeframe(timestamp, time_zone, timeframe: int, open_time: str = None) -> str:
    """
    Calculate the next timestamp based on the given timeframe in seconds.
    The timestamp will be rounded up to the nearest multiple of the timeframe.

    Args:
        timestamp: timestamp in seconds.
        time_zone (int): The timezone of the timestamp.
        timeframe (int): The timeframe in seconds to round to.
        open_time (str): The opening time of the timestamp.

    Returns:
        str: The next rounded date based on the timeframe.
    """
    now_date = datetime.fromtimestamp(timestamp)
    if open_time:
        current_year = now_date.year
        if len(open_time.split()[-1]) == 5:
            open_time = f"{open_time}:00"

        full_date_time = open_time
        if len(open_time.split('/')[0]) != 4:
            full_date_time = f"{current_year}/{open_time}"

        date_time_obj = datetime.strptime(full_date_time, "%Y/%d/%m %H:%M:%S")
        next_time = date_time_obj.replace(second=0, microsecond=0) - timedelta(seconds=time_zone)
    else:
        seconds_passed = now_date.second + now_date.minute * 60
        next_timeframe_seconds = ((seconds_passed // timeframe) + 2) * timeframe
        next_time = now_date + timedelta(seconds=next_timeframe_seconds - seconds_passed)
        next_time = next_time.replace(second=0, microsecond=0) - timedelta(seconds=time_zone)

    return next_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def get_expiration_time(timestamp, duration):
    now = datetime.now()
    new_date = now.replace(second=0, microsecond=0)
    exp = new_date + timedelta(seconds=duration)
    exp_date = exp.replace(second=0, microsecond=0)
    return int(date_to_timestamp(exp_date))


def get_period_time(duration):
    now = datetime.now()
    period_date = now - timedelta(seconds=duration)
    return int(date_to_timestamp(period_date))


def get_remaning_time(timestamp):
    now_date = datetime.fromtimestamp(timestamp)
    exp_date = now_date.replace(second=0, microsecond=0)
    if (int(date_to_timestamp(exp_date + timedelta(minutes=1))) - timestamp) > 30:
        exp_date = exp_date + timedelta(minutes=1)
    else:
        exp_date = exp_date + timedelta(minutes=2)
    exp = []
    for _ in range(5):
        exp.append(date_to_timestamp(exp_date))
        exp_date = exp_date + timedelta(minutes=1)
    idx = 11
    index = 0
    now_date = datetime.fromtimestamp(timestamp)
    exp_date = now_date.replace(second=0, microsecond=0)
    while index < idx:
        if int(exp_date.strftime("%M")) % 15 == 0 and (int(date_to_timestamp(exp_date)) - int(timestamp)) > 60 * 5:
            exp.append(date_to_timestamp(exp_date))
            index = index + 1
        exp_date = exp_date + timedelta(minutes=1)
    remaning = []
    for idx, t in enumerate(exp):
        if idx >= 5:
            dr = 15 * (idx - 4)
        else:
            dr = idx + 1
        remaning.append((dr, int(t) - int(time.time())))
    return remaning


def get_server_timer(time_offset_seconds: int) -> int:
    """
    Returns the server (UTC) timestamp based on local time and offset.

    :param time_offset_seconds: The offset in seconds between local time and UTC.
                                Example: -10800 for UTC-3.
    :return: An integer representing the server time as a Unix timestamp (UTC).
    """
    local_time = datetime.now()
    server_time = local_time - timedelta(seconds=time_offset_seconds)
    return int(server_time.timestamp())
