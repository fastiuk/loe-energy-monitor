"""Prykarpattiaoblenergo (Ivano-Frankivsk): ``GET https://be-svitlo.oe.if.ua/schedule-by-queue?queue=1.1``.

::

    [{"eventDate": "17.10.2026",
      "queues": {"1.1": [{"shutdownHours": "10:00-12:00", "from": "10:00", "to": "12:00", "status": 1}]},
      "createdAt": "17.10.2026 09:48", "scheduleApprovedSince": "17.10.2026 18:54"}]

Like the site: a queue with intervals has outages, an empty list on a listed day means power
all day, a missing queue means no data. A response may carry more queues than asked for;
only the queues still missing are requested.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage
from ._text import parse_intervals
from .base import Provider, fail

URL = "https://be-svitlo.oe.if.ua/schedule-by-queue"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_STAMP_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, str) and (m := _STAMP_RE.fullmatch(value.strip())):
        try:
            return datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4]), int(m[5]), tzinfo=KYIV)
        except ValueError:
            return None
    return None


def merge_response(payload: Any, schedules: dict[date, DaySchedule]) -> None:
    """Add the days and queues of one response to ``schedules``."""
    if not isinstance(payload, list) or len(payload) > 7:
        fail("Prykarpattia: expected a list of days")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not (m := _DATE_RE.fullmatch(str(item.get("eventDate")))):
            fail("Prykarpattia: day without a valid eventDate")
        try:
            day = date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            fail("Prykarpattia: invalid eventDate")
        queues = item.get("queues")
        if not isinstance(queues, dict):
            continue
        current = schedules.get(day) or DaySchedule(day)
        groups, unknown = dict(current.groups), dict(current.unknown)
        for group, intervals in queues.items():
            if group not in GROUPS or group in groups or group in unknown:
                continue
            outages = _intervals(day, intervals)
            if outages is None:
                unknown[group] = "Нерозпізнаний графік"
            else:
                groups[group] = outages
        updated = _stamp(item.get("scheduleApprovedSince")) or _stamp(item.get("createdAt"))
        schedules[day] = DaySchedule(day, updated or current.updated_at, groups, unknown)


def _intervals(day: date, intervals: Any) -> tuple[Outage, ...] | None:
    if not isinstance(intervals, list) or len(intervals) > 48:
        return None
    if not intervals:
        return ()
    parts = []
    for i in intervals:
        if not isinstance(i, dict) or not (
            _TIME_RE.fullmatch(str(i.get("from"))) and _TIME_RE.fullmatch(str(i.get("to")))
        ):
            return None
        parts.append(f"{i['from']}-{i['to']}")
    return parse_intervals(day, "; ".join(parts))


class Prykarpattia(Provider):
    hosts = frozenset({"be-svitlo.oe.if.ua"})
    min_interval = 300
    official_api = True

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        schedules: dict[date, DaySchedule] = {}
        for group in GROUPS:
            if schedules and all(s.mentions(group) for s in schedules.values()):
                continue
            resp = await http.get(URL, params={"queue": group}, headers={"Accept": "application/json"})
            merge_response(resp.json(), schedules)
            if not schedules:
                break  # nothing published for any day
        return dict(sorted(schedules.items()))
