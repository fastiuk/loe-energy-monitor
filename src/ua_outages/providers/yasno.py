"""YASNO public API: Kyiv (ДТЕК КЕМ) and Dnipropetrovsk oblast (ДТЕК ДнЕМ, ЦЕК).

``GET https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/{region}/dsos/{dso}/planned-outages``::

    {"1.1": {"today": {"slots": [{"start": 0, "end": 810, "type": "NotPlanned"},
                                 {"start": 810, "end": 1020, "type": "Definite"}],
                       "date": "2026-10-18T00:00:00+03:00", "status": "ScheduleApplies"},
             "tomorrow": {..., "status": "WaitingForSchedule"},
             "updatedOn": "2026-10-18T12:53:46+00:00"}, ...}

Slot ends are minutes from local midnight.

The group of an address: ``.../addresses/v2/{cities,streets,houses}`` search by substring
(``query``, at least 3 letters, up to 20 results) and ``.../addresses/v2/group`` answers
``{"group": 19, "subgroup": 1}``. Dnipropetrovsk oblast has a city step, and every later
request there needs ``cityId``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, at_minute, merge_outages
from ._address import clean_label, entries, numeric_id, pick, require_id, search_term, strip_kind
from .base import AddressLookup, Choice, Provider, Resolution, Step, fail

BASE_URL = "https://app.yasno.ua/api/blackout-service/public/shutdowns"
ADDRESS_URL = f"{BASE_URL}/addresses/v2"
MAX_GROUPS = 200
MAX_SLOTS = 200
_GROUP_RE = re.compile(r"\d{1,3}\.\d{1,2}")

EMERGENCY = "Діють аварійні відключення, графік не застосовується"


@dataclass
class _Day:
    groups: dict[str, tuple[Outage, ...]] = field(default_factory=dict)
    unknown: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    updated: list[datetime] = field(default_factory=list)


def _slots(day: date, slots: Any) -> tuple[Outage, ...] | None:
    if not isinstance(slots, list) or len(slots) > MAX_SLOTS:
        return None
    outages = []
    for slot in slots:
        if not isinstance(slot, dict):
            return None
        start, end, kind = slot.get("start"), slot.get("end"), slot.get("type")
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= 1440):
            return None
        if kind == "NotPlanned":
            continue
        if kind == "Definite":
            possible = False
        elif isinstance(kind, str) and kind.startswith(("Possib", "Probab")):
            possible = True
        else:
            return None
        outages.append(Outage(at_minute(day, start), at_minute(day, end), possible))
    return merge_outages(outages)


def parse_planned(payload: Any) -> dict[date, DaySchedule]:
    if not isinstance(payload, dict) or not payload or len(payload) > MAX_GROUPS:
        fail("YASNO: expected an object keyed by group")
    days: dict[date, _Day] = {}
    for group, data in payload.items():
        if not _GROUP_RE.fullmatch(group) or not isinstance(data, dict):
            fail(f"YASNO: unexpected group entry {group[:20]!r}")
        updated = _datetime(data.get("updatedOn"))
        for key in ("today", "tomorrow"):
            entry = data.get(key)
            if not isinstance(entry, dict) or entry.get("status") == "WaitingForSchedule":
                continue
            if (start := _datetime(entry.get("date"))) is None:
                fail(f"YASNO: no date for {group} {key}")
            day = start.date()
            bucket = days.setdefault(day, _Day())
            if updated:
                bucket.updated.append(updated)
            status = entry.get("status")
            if status == "NoOutages":
                bucket.groups[group] = ()
            elif status == "ScheduleApplies":
                outages = _slots(day, entry.get("slots"))
                if outages is None:
                    bucket.unknown[group] = "Нерозпізнаний графік"
                else:
                    bucket.groups[group] = outages
            elif status == "EmergencyShutdowns":
                bucket.unknown[group] = EMERGENCY
                if EMERGENCY not in bucket.notes:
                    bucket.notes.append(EMERGENCY)
            else:
                bucket.unknown[group] = f"Невідомий стан: {str(status)[:50]}"
    return {
        day: DaySchedule(
            day,
            max(b.updated) if b.updated else None,
            b.groups,
            b.unknown,
            tuple(b.notes),
        )
        for day, b in sorted(days.items())
    }


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
        return moment.astimezone(KYIV) if moment.tzinfo else None
    except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
        return None


class Yasno(Provider):
    hosts = frozenset({"app.yasno.ua"})
    min_interval = 180

    def __init__(self, region: int, dso: int) -> None:
        self.region = region
        self.dso = dso

    @property
    def name(self) -> str:
        return f"YASNO {self.region}/{self.dso}"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        url = f"{BASE_URL}/regions/{self.region}/dsos/{self.dso}/planned-outages"
        resp = await http.get(url, headers={"Accept": "application/json"})
        return parse_planned(resp.json())


def parse_choices(payload: Any) -> list[Choice]:
    """``[{"id": 1011, "value": "вул. Хрещатик"}, ...]``"""
    choices = []
    for item in entries(payload, "YASNO addresses"):
        if isinstance(item, dict) and (id_ := numeric_id(item.get("id"))) and (label := clean_label(item.get("value"))):
            choices.append(Choice(id_, label))
    return choices


def parse_group(payload: Any) -> Resolution:
    """``{"group": 19, "subgroup": 1}`` → group "19.1"."""
    group = payload.get("group") if isinstance(payload, dict) else None
    subgroup = payload.get("subgroup") if isinstance(payload, dict) else None
    if not all(isinstance(n, int) and not isinstance(n, bool) and 0 < n < 100 for n in (group, subgroup)):
        return Resolution(())
    return Resolution((f"{group}.{subgroup}",))


_HOUSE_NUMBER_RE = re.compile(r"[0-9]{1,5}")


class YasnoLookup(AddressLookup):
    hosts = frozenset({"app.yasno.ua"})
    min_query = 3

    def __init__(self, region: int, dso: int, *, cities: bool, city_example: str = "", street_example: str = "") -> None:
        self.region = region
        self.dso = dso
        self.cities = cities
        city = Step("Напишіть назву населеного пункту", city_example)
        street = Step("Напишіть назву вулиці", street_example)
        house = Step("Напишіть номер будинку", "12", house=True)
        self.steps = ((city,) if cities else ()) + (street, house)

    @property
    def name(self) -> str:
        return f"YASNO {self.region}/{self.dso}"

    def _params(self, path: Sequence[str]) -> dict[str, str]:
        params = {"regionId": str(self.region), "dsoId": str(self.dso)}
        ids = [require_id(p) for p in path]
        names = (["cityId"] if self.cities else []) + ["streetId", "houseId"]
        params.update(zip(names, ids, strict=False))
        return params

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        step = self.steps[len(path)]
        endpoint = "houses" if step.house else "streets" if len(path) == int(self.cities) else "cities"
        if step.house:
            # The directory writes "8А" where people type "8-а": search by the number, filter here.
            number = _HOUSE_NUMBER_RE.search(query)
            terms = [number[0] if number else strip_kind(query).strip()]
        else:
            whole, longest = strip_kind(query).strip(), search_term(query)
            terms = [whole] + ([longest] if longest != whole.casefold() else [])
        for term in terms:
            if len(term) < (1 if step.house else self.min_query):
                continue
            params = {**self._params(path), "query": term}
            resp = await http.get(f"{ADDRESS_URL}/{endpoint}", params=params, headers={"Accept": "application/json"})
            if found := pick(parse_choices(resp.json()), query):
                return found
        return []

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        resp = await http.get(f"{ADDRESS_URL}/group", params=self._params(path), headers={"Accept": "application/json"})
        return parse_group(resp.json())
