import time
import calendar
from datetime import datetime, timedelta


def get_timestamp():
    return calendar.timegm(time.gmtime())


def date_to_timestamp(dt):
    return time.mktime(dt.timetuple())


def timestamp_to_date(timestamp):
    return datetime.fromtimestamp(timestamp)


def get_expiration_time_quotex(timestamp, duration):
    now_date = datetime.fromtimestamp(timestamp)
    shift = 0
    if now_date.second >= 30:
        shift = 1
    exp_date = now_date.replace(second=0, microsecond=0)
    exp_date = exp_date + timedelta(minutes=int(duration / 60) + shift)
    return date_to_timestamp(exp_date)


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
