"""Sumyoblenergo: ``GET https://www.soe.com.ua/api/vidkluchenya/get_all_data_disconnections_api``.

::

    {"data": [{"cherga_n": "1.1", "no_data": false, "last_update": "Оновлено: 18.10.2026 14:30",
               "lst_time_disc": [{"start_time": "22:00", "end_time": "00:00"}]}, ...]}

The API serves only the current day and carries no date apart from ``last_update``, so the
schedule is taken as today's while that stamp is recent.

Not in the registry since 19.10.2026: the API answers 403 ("X-Debug-API: HIT-PROTECTED-SUB-API")
to everything but the operator's own site, and access controls are not worked around. Kept for
when it opens again; then also check what it serves after an evening update for tomorrow.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage
from ._text import parse_intervals
from .base import Provider, fail

URL = "https://www.soe.com.ua/api/vidkluchenya/get_all_data_disconnections_api"
MAX_AGE = timedelta(hours=36)
_UPDATED_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s{1,3}(\d{2}):(\d{2})")
_GROUP_RE = re.compile(r"\d\.\d")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")


def parse(payload: Any, now: datetime) -> dict[date, DaySchedule]:
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or len(items) > 50:
        fail("Sumy: response has no data list")
    day = now.date()
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    updated: datetime | None = None
    for item in items:
        group = item.get("cherga_n") if isinstance(item, dict) else None
        if not isinstance(group, str) or not _GROUP_RE.fullmatch(group):
            continue
        if m := _UPDATED_RE.search(str(item.get("last_update"))[:100]):
            try:
                stamp = datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4]), int(m[5]), tzinfo=KYIV)
                updated = max(updated, stamp) if updated else stamp
            except ValueError:
                pass
        if item.get("no_data"):
            continue
        intervals = item.get("lst_time_disc") or []
        if not isinstance(intervals, list) or len(intervals) > 48:
            unknown[group] = "Нерозпізнаний графік"
            continue
        text = "; ".join(
            f"{i.get('start_time')}-{i.get('end_time')}"
            for i in intervals
            if isinstance(i, dict)
            and _TIME_RE.fullmatch(str(i.get("start_time")))
            and _TIME_RE.fullmatch(str(i.get("end_time")))
        )
        outages = parse_intervals(day, text) if text else ()
        if outages is None or (text.count(";") + 1 if text else 0) != len(intervals):
            unknown[group] = "Нерозпізнаний графік"
        else:
            groups[group] = outages
    if updated is None or now - updated > MAX_AGE or updated.date() > day:
        return {}
    return {day: DaySchedule(day, updated, groups, unknown)}


class Sumy(Provider):
    hosts = frozenset({"www.soe.com.ua"})
    #: The site itself refreshes every 15 minutes.
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL, headers={"Accept": "application/json"})
        return parse(resp.json(), now)
