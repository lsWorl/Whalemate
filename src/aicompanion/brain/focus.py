"""专注模式与作息：全应用只认这一份设置（surfcore 的"只存一份真相"）。"""
from __future__ import annotations

from datetime import datetime, timedelta

FOCUS_KEY = 'focus_until'


def focus_until(database, now: datetime | None = None) -> datetime | None:
    value = database.get_setting(FOCUS_KEY)
    if not value: return None
    try:
        until = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return until if until > (now or datetime.now()) else None


def is_focused(database, now: datetime | None = None) -> bool:
    return focus_until(database, now) is not None


def set_focus(database, minutes: int | None, now: datetime | None = None) -> datetime | None:
    """minutes=None 表示关闭专注模式。"""
    if not minutes:
        database.set_setting(FOCUS_KEY, ''); return None
    until = (now or datetime.now())+timedelta(minutes=minutes)
    database.set_setting(FOCUS_KEY, until.isoformat(timespec='seconds'))
    return until


def quiet_hours(database) -> tuple[int, int]:
    """作息：几点开始安静、几点醒来（沿用设置里的 quiet_start / quiet_end）。"""
    return int(database.get_setting('quiet_start', 23)), int(database.get_setting('quiet_end', 8))


def in_quiet_hours(database, hour: int) -> bool:
    start, end = quiet_hours(database)
    return (hour >= start or hour < end) if start > end else start <= hour < end
