"""Mykolaivoblenergo: the off.energy.mk.ua JSON API.

::

    GET /api/outage-queue/by-type/3   → [{"id": 14, "name": "1.1", ...}, ...]       (ids are not contiguous)
    GET /api/schedule/time-series     → [{"id": 1, "start": "00:00:00", "end": "00:30:00"}, ...]
    GET /api/v2/schedule/active       → [{"from": "2026-10-16T21:00:00.000000Z", "to": "...",
                                          "series": [{"time_series_id": 22, "outage_queue_id": 14,
                                                      "type": "OFF"}, ...]}, ...]

One item of ``active`` is one day (``from`` is Kyiv midnight in UTC). ``type`` is ``OFF`` or
``SURE_OFF`` (no power), ``PROBABLY_OFF`` (may be none) or ``ENABLE``.

The group of an address: ``/api/addr/filii`` (districts) → ``/filii/{id}/ns`` → ``/ns/{id}/street``
→ ``/street/{id}/dom`` → ``/dom/{id}/outage-queue``, whole lists at each level.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, at, merge_outages
from ._address import clean_label, entries, normalize_group, numeric_id, pick, require_id
from .base import AddressLookup, Choice, Kind, Provider, Resolution, Step, fail

BASE_URL = "https://off.energy.mk.ua/api"
MAX_ITEMS = 5000
_TIME_RE = re.compile(r"(\d{2}):(\d{2})(?::\d{2})?")
_GROUP_RE = re.compile(r"\d\.\d")
TYPES = {"OFF": False, "SURE_OFF": False, "PROBABLY_OFF": True}


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        fail(f"Mykolaiv: {what} is not a list")
    return value


def parse(queues: Any, series: Any, active: Any) -> dict[date, DaySchedule]:
    groups = {
        q["id"]: q["name"]
        for q in _list(queues, "queues")
        if isinstance(q, dict) and isinstance(q.get("name"), str) and _GROUP_RE.fullmatch(q["name"])
    }
    slots = {}
    for s in _list(series, "time series"):
        if not isinstance(s, dict):
            continue
        start, end = _TIME_RE.fullmatch(str(s.get("start"))), _TIME_RE.fullmatch(str(s.get("end")))
        if start and end:
            begin, finish = (int(start[1]), int(start[2])), (int(end[1]), int(end[2]))
            slots[s.get("id")] = (begin, finish if finish != (0, 0) else (24, 0))
    if not groups or not slots:
        fail("Mykolaiv: no queues or time slots")

    schedules = {}
    for item in _list(active, "active schedules"):
        if not isinstance(item, dict):
            continue
        try:
            day = datetime.fromisoformat(str(item.get("from")).replace("Z", "+00:00")).astimezone(KYIV).date()
        except (ValueError, OverflowError):
            fail("Mykolaiv: schedule has no valid 'from'")
        outages: dict[str, list[Outage]] = defaultdict(list)
        for cell in _list(item.get("series"), "series"):
            if not isinstance(cell, dict) or (possible := TYPES.get(cell.get("type"))) is None:
                continue
            group, slot = groups.get(cell.get("outage_queue_id")), slots.get(cell.get("time_series_id"))
            if group and slot:
                (h1, m1), (h2, m2) = slot
                outages[group].append(Outage(at(day, h1, m1), at(day, h2, m2), possible))
        schedules[day] = DaySchedule(
            day, groups={g: merge_outages(outages.get(g, ())) for g in sorted(set(groups.values()))}
        )
    return dict(sorted(schedules.items()))


class Mykolaiv(Provider):
    hosts = frozenset({"off.energy.mk.ua"})
    min_interval = 300
    kind = Kind.API
    page = f"{BASE_URL}/v2/schedule/active"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        queues = (await http.get(f"{BASE_URL}/outage-queue/by-type/3")).json()
        series = (await http.get(f"{BASE_URL}/schedule/time-series")).json()
        active = (await http.get(f"{BASE_URL}/v2/schedule/active")).json()
        return parse(queues, series, active)


#: Each level of the directory: URL template, id field, name field.
_LEVELS = (
    ("{base}/addr/filii", "idfilial", "fullname"),
    ("{base}/addr/filii/{id}/ns", "idnaspunkt", "naznaspunkt"),
    ("{base}/addr/ns/{id}/street", "idstreet", "nazstreet"),
    ("{base}/addr/street/{id}/dom", "iddom", "nazdom"),
)


def parse_level(payload: Any, id_field: str, name_field: str) -> list[Choice]:
    choices = []
    for item in entries(payload, "Mykolaiv addresses"):
        if isinstance(item, dict) and (id_ := numeric_id(item.get(id_field))) and (name := clean_label(item.get(name_field))):
            choices.append(Choice(id_, name))
    return choices


def parse_queues(payload: Any) -> Resolution:
    """``[{"outage": {"name": "2.2", "type_id": 3}, "outageType": {"name": "ГПВ"}}, ...]``: only ГПВ counts."""
    groups = []
    for item in entries(payload, "Mykolaiv queues"):
        outage = item.get("outage") if isinstance(item, dict) else None
        kind = item.get("outageType") if isinstance(item, dict) else None
        if not isinstance(outage, dict) or not isinstance(kind, dict) or kind.get("name") != "ГПВ":
            continue
        if (group := normalize_group(outage.get("name"))) and group not in groups:
            groups.append(group)
    return Resolution(tuple(groups))


class MykolaivLookup(AddressLookup):
    hosts = frozenset({"off.energy.mk.ua"})
    steps = (
        Step("Оберіть дільницю Миколаївобленерго (місто Миколаїв — «Миколаївський РЕМ»)", browse=True, in_label=False),
        Step("Напишіть назву населеного пункту", "Баштанка", browse=True),
        Step("Напишіть назву вулиці", "Садова", browse=True),
        Step("Напишіть номер будинку", "12", browse=True, house=True),
    )

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        template, id_field, name_field = _LEVELS[len(path)]
        url = template.format(base=BASE_URL, id=require_id(path[-1]) if path else "")
        resp = await http.get(url, headers={"Accept": "application/json"})
        return pick(parse_level(resp.json(), id_field, name_field), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        url = f"{BASE_URL}/addr/dom/{require_id(path[-1])}/outage-queue"
        return parse_queues((await http.get(url, headers={"Accept": "application/json"})).json())
