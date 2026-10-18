"""Small, predictable natural-date parser (local naive time). Ambiguous input returns None instead of guessing."""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WD = "|".join(WEEKDAYS)


def add_months(dt: datetime, n: int) -> datetime:
    idx = dt.month - 1 + n
    year, month = dt.year + idx // 12, idx % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def _tod(h: str, m: Optional[str], ap: Optional[str]) -> Optional[Tuple[int, int]]:
    """Time of day; a bare hour with no am/pm and no minutes is ambiguous, so it is rejected."""
    if not ap and m is None:
        return None
    hh, mm = int(h), int(m) if m else 0
    if ap:
        if not 1 <= hh <= 12:
            return None
        hh = hh % 12 + (12 if ap == "pm" else 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[t ](\d{1,2}):(\d{2}))?")
_REL = re.compile(r"in (\d+) (minute|hour|day|week)s?")
_BARE = re.compile(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?")
_DAY = re.compile(
    rf"(?P<day>today|tonight|tomorrow|(?:next\s+)?(?P<wd>{_WD}))"
    r"(?:\s+(?:at\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?)?"
)


def parse_when(text: str, now: datetime) -> Optional[Tuple[datetime, bool]]:
    """Return (datetime, all_day) or None."""
    t = " ".join(str(text).lower().strip().split())
    if not t:
        return None
    m = _ISO.fullmatch(t)
    if m:
        try:
            base = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        if m.group(4) is None:
            return base, True
        hh, mm = int(m.group(4)), int(m.group(5))
        return (base.replace(hour=hh, minute=mm), False) if hh <= 23 and mm <= 59 else None
    m = _REL.fullmatch(t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "minute":
            return (now + timedelta(minutes=n)).replace(second=0, microsecond=0), False
        if unit == "hour":
            return (now + timedelta(hours=n)).replace(second=0, microsecond=0), False
        days = n if unit == "day" else n * 7
        return (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0), True
    m = _BARE.fullmatch(t)
    if m and (m.group(2) is not None or m.group(3)):
        tod = _tod(m.group(1), m.group(2), m.group(3))
        if tod is None:
            return None
        cand = now.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
        return (cand if cand > now else cand + timedelta(days=1)), False
    m = _DAY.fullmatch(t)
    if m:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = m.group("day")
        if day in ("today", "tonight"):
            d = today
        elif day == "tomorrow":
            d = today + timedelta(days=1)
        else:
            delta = (WEEKDAYS.index(m.group("wd")) - today.weekday()) % 7 or 7
            d = today + timedelta(days=delta)
        if m.group("h") is not None:
            tod = _tod(m.group("h"), m.group("m"), m.group("ap"))
            return (d.replace(hour=tod[0], minute=tod[1]), False) if tod else None
        if day == "tonight":
            return d.replace(hour=20, minute=0), False
        return d, True
    return None


_DUE_TAIL = re.compile(
    r"\s+(?:(?:by|on|due|for|before)\s+)?"
    rf"((?:today|tonight|tomorrow|(?:next\s+)?(?:{_WD})|in\s+\d+\s+(?:minute|hour|day|week)s?"
    r"|\d{4}-\d{2}-\d{2}(?:[t ]\d{1,2}:\d{2})?)"
    r"(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)\s*$",
    re.I,
)


def extract_due_suffix(text: str) -> Tuple[str, Optional[str]]:
    """'finish the report tomorrow' -> ('finish the report', 'tomorrow')."""
    m = _DUE_TAIL.search(text)
    if not m:
        return text.strip(), None
    return text[: m.start()].strip(), m.group(1)
