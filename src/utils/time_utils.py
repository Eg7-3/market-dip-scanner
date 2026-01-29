from __future__ import annotations

from datetime import datetime, time, timedelta

import pytz


def now_tz(tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def is_market_open(tz_name: str, cooldown_minutes_after_open: int = 0) -> bool:
    """
    Rough US market hours checker (regular session 9:30-16:00 ET).
    """
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    open_time = tz.localize(datetime.combine(now.date(), time(9, 30)))
    open_time = open_time + timedelta(minutes=cooldown_minutes_after_open)
    close_time = tz.localize(datetime.combine(now.date(), time(16, 0)))
    return open_time <= now <= close_time


def is_weekend(tz_name: str) -> bool:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz).weekday() >= 5
