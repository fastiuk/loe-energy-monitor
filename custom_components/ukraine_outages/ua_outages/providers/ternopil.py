"""Ternopiloblenergo: the poweron.toe.com.ua API (inneti platform), one address per request.

::

    GET https://api-poweron.toe.com.ua/api/a_gpv_g?after=…&before=…&time=<cityId><streetId><house>
    X-debug-key: base64("<cityId>/<streetId>/<house>")
    → {"hydra:member": [{"dateGraph": "2026-10-17T00:00:00Z", "dateCreate": "2026-10-17T18:52:36Z",
                         "dataJson": {"3.2": {"times": {"00:00": "0", "19:30": "10", "20:00": "1", ...}}}}]}

The answer holds only the group(s) of that address, whatever ``group[]`` asks for, and
Ternopil city alone lacks 4 of the 12 subqueues: the bot polls one subscriber's address per
group. ``"0"`` — power on, ``"1"`` — off, ``"10"`` — maybe off; ``hydra:member`` is ``null``
when nothing is published. Archive records may carry arbitrary HTML in other fields: only
``dataJson`` is read.

The address directory: ``/pw_cities?name=`` and ``/pw_streets?city.id=&name=`` search by
substring, ``/pw-accounts/building-names?cityId=&streetId=`` lists buildings with ``chergGpv``.
"""

from __future__ import annotations

import base64
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._address import clean_label, entries, normalize_group, numeric_id, pick, require_id, search_term
from .base import AddressLookup, AddressSource, Choice, Resolution, Step, fail

API_URL = "https://api-poweron.toe.com.ua/api"
STATES = {"0": Slot.ON, "1": Slot.OFF, "10": Slot.MAYBE}
TIMES = tuple(f"{n // 2:02d}:{n % 2 * 30:02d}" for n in range(48))
MAX_DAYS = 10
MAX_GROUPS = 20
#: "<cityId>/<streetId>/<building name>"; building names may hold a slash ("33/38").
_ADDRESS_RE = re.compile(r"([0-9]{1,9})/([0-9]{1,9})/([^\x00-\x1f\x7f]{1,30})")


def address_key(city: str, street: str, building: str) -> str | None:
    key = f"{city}/{street}/{building}"
    return key if _ADDRESS_RE.fullmatch(key) else None


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(KYIV) if moment.tzinfo else None
    except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
        return None


def _outages(day: date, data: Any) -> tuple[Outage, ...] | None:
    times = data.get("times") if isinstance(data, dict) else None
    if not isinstance(times, dict):
        return None
    states = [STATES.get(times.get(t)) for t in TIMES]
    if None in states:
        return None
    return outages_from_slots(day, states)  # type: ignore[arg-type]


def parse(payload: Any) -> dict[date, DaySchedule]:
    if not isinstance(payload, dict):
        fail("Ternopil: expected an object")
    members = payload.get("hydra:member")
    if members is None:
        return {}
    if not isinstance(members, list) or len(members) > MAX_DAYS:
        fail("Ternopil: hydra:member is not a short list")
    schedules: dict[date, DaySchedule] = {}
    for member in members:
        if not isinstance(member, dict) or (start := _datetime(member.get("dateGraph"))) is None:
            fail("Ternopil: a schedule without dateGraph")
        data = member.get("dataJson")
        if not isinstance(data, dict) or len(data) > MAX_GROUPS:
            fail("Ternopil: dataJson is not a small object")
        day = start.date()
        groups: dict[str, tuple[Outage, ...]] = {}
        unknown: dict[str, str] = {}
        for key, value in data.items():
            if (group := normalize_group(key)) is None:
                continue
            if (outages := _outages(day, value)) is None:
                unknown[group] = "Нерозпізнаний графік"
            else:
                groups[group] = outages
        schedule = DaySchedule(day, _datetime(member.get("dateCreate")), groups, unknown)
        previous = schedules.get(day)
        if previous is None or (schedule.updated_at or start) >= (previous.updated_at or start):
            schedules[day] = schedule
    return dict(sorted(schedules.items()))


class Ternopil(AddressSource):
    hosts = frozenset({"api-poweron.toe.com.ua"})
    min_interval = 300
    request_gap = 1.0
    official_api = True

    def valid_address(self, address: str) -> bool:
        return isinstance(address, str) and _ADDRESS_RE.fullmatch(address) is not None

    async def fetch_address(self, http: Fetcher, now: datetime, address: str) -> dict[date, DaySchedule]:
        if not self.valid_address(address):
            raise ValueError("not a Ternopil address key")
        today = now.astimezone(KYIV).date()
        # The same window the site asks for: yesterday noon to tomorrow (dates written as UTC).
        params = {
            "after": f"{today - timedelta(days=1):%Y-%m-%d}T12:00:00+00:00",
            "before": f"{today + timedelta(days=1):%Y-%m-%d}T00:00:00+00:00",
            "time": address.replace("/", ""),
        }
        headers = {
            "Accept": "application/ld+json",
            "X-debug-key": base64.b64encode(address.encode()).decode("ascii"),
        }
        resp = await http.get(f"{API_URL}/a_gpv_g", params=params, headers=headers)
        return parse(resp.json())


def parse_cities(payload: Any) -> list[Choice]:
    """``[{"id": 1032, "name": "Тернопіль", "otg": {"name": "Тернопільська"}}, ...]``"""
    choices = []
    for item in entries(payload, "Ternopil cities"):
        if not isinstance(item, dict) or not (id_ := numeric_id(item.get("id"))) or not (name := clean_label(item.get("name"))):
            continue
        otg = item.get("otg")
        community = clean_label(otg.get("name"), 40) if isinstance(otg, dict) else None
        choices.append(Choice(id_, f"{name} ({community} громада)" if community else name))
    return choices


def parse_streets(payload: Any) -> list[Choice]:
    choices = []
    for item in entries(payload, "Ternopil streets"):
        if isinstance(item, dict) and (id_ := numeric_id(item.get("id"))) and (name := clean_label(item.get("name"))):
            choices.append(Choice(id_, name))
    return choices


def parse_buildings(payload: Any) -> dict[str, Counter[str]]:
    """``{"buildingNames": [{"buildingName": "2-А", "chergGpv": "1.2"}, ...]}`` → name → ``chergGpv`` values."""
    buildings: dict[str, Counter[str]] = {}
    for item in entries(payload, "Ternopil buildings", "buildingNames"):
        if not isinstance(item, dict) or not (name := clean_label(item.get("buildingName"), 30)):
            continue
        values = buildings.setdefault(name, Counter())
        if value := clean_label(item.get("chergGpv"), 40):
            values[value] += 1
    return buildings


class TernopilLookup(AddressLookup):
    hosts = frozenset({"api-poweron.toe.com.ua"})
    steps = (
        Step("Напишіть назву населеного пункту", "Тернопіль"),
        Step("Напишіть назву вулиці", "Руська"),
        Step("Напишіть номер будинку", "1", browse=True, house=True),
    )

    async def _buildings(self, http: Fetcher, city: str, street: str) -> dict[str, Counter[str]]:
        params = {"cityId": require_id(city), "streetId": require_id(street)}
        resp = await http.get(f"{API_URL}/pw-accounts/building-names", params=params, headers={"Accept": "application/json"})
        return parse_buildings(resp.json())

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        if len(path) == 2:
            return pick((Choice(b, b) for b in await self._buildings(http, *path)), query)
        if len(search_term(query)) < self.min_query:
            return []
        headers = {"Accept": "application/json"}
        if not path:
            params = {"pagination": "false", "name": search_term(query)}
            resp = await http.get(f"{API_URL}/pw_cities", params=params, headers=headers)
            return pick(parse_cities(resp.json()), query)
        params = {"pagination": "false", "city.id": require_id(path[0]), "name": search_term(query)}
        resp = await http.get(f"{API_URL}/pw_streets", params=params, headers=headers)
        return pick(parse_streets(resp.json()), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != 3:
            raise ValueError("incomplete address")
        city, street, building = path
        values = (await self._buildings(http, city, street)).get(building)
        if values is None or (address := address_key(city, street, building)) is None:
            raise ValueError("no such building")
        groups = []
        other = []
        for value, _ in values.most_common():
            if (group := normalize_group(value)) and group not in groups:
                groups.append(group)
            elif not group:
                other.append(value)
        return Resolution(tuple(groups), tuple(other), address)
