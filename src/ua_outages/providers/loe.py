"""Lvivoblenergo (LOE): the text schedule in ``rawHtml`` of the ``photo-grafic`` menu.

Ported from fastiuk/loe-energy-monitor. Known sentence formats::

    Група 1.1. Електроенергії немає з 20:00 до 22:30.
    Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 22:30.
    Група 2.1. Електроенергії немає з 08:00 до 10:30 з 14:00 до 17:00.
    Група 3.1. Електроенергії немає з 21:00 до 02:00.
    Група 1.1. Електроенергія є.

The group of an address comes from the directory on ``power-api.loe.lviv.ua``:
``/pw_cities?name=`` and ``/pw_streets?city.id=&name=`` search by substring, and
``/pw_accounts?city.id=&street.id=`` lists the street's buildings with ``chergGpv``. That
list has a row per consumer (no account numbers or names, ~2 MB for a long street), so it is
fetched once and cached; Cloudflare answers HTTP 429 to quick series of requests.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import re
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage
from ._address import clean_label, entries, normalize_group, numeric_id, pick, require_id, search_term
from ._text import html_lines, parse_intervals
from .base import AddressLookup, Choice, Kind, Provider, Resolution, Step, fail, url_on_host

log = logging.getLogger(__name__)

BASE_URL = "https://api.loe.lviv.ua"
SCHEDULE_URL = f"{BASE_URL}/api/menus"
DIRECTORY_URL = "https://power-api.loe.lviv.ua/api"
MAX_RAW_HTML = 100_000  # a real day's schedule is about 1 KB
MAX_SENTENCE = 300
MAX_NOTES = 10

_DAY_RE = re.compile(r"графік\b[^\n]{0,200}?\bна\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE)
_UPDATED_RE = re.compile(
    r"станом\s+на\s+(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE
)
_GROUP_RE = re.compile(r"Груп[аи]\s+(\d)\s*\.\s*(\d)\.?", re.IGNORECASE)
_POWER_ON_RE = re.compile(r"електроенергія\s+є|відключень\s+не", re.IGNORECASE)


def _outages(day: date, sentence: str) -> tuple[Outage, ...] | None:
    """Outages from one group sentence; ``()`` when power is on, ``None`` if unrecognised."""
    if (outages := parse_intervals(day, sentence)) is not None:
        return outages
    if _POWER_ON_RE.search(sentence):
        return ()
    return None


def parse_day(raw_html: str, image_url: str | None = None) -> DaySchedule | None:
    """Parse one day's ``rawHtml``; ``None`` if it is empty, too long or has no date header."""
    if len(raw_html or "") > MAX_RAW_HTML:
        log.warning("LOE schedule text is %d characters long, ignoring it", len(raw_html))
        return None
    lines = html_lines(raw_html or "")
    text = "\n".join(lines)
    if not (m := _DAY_RE.search(text)):
        if text:
            log.warning("LOE schedule text has no date header: %.200s", text)
        return None
    try:
        day = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        log.warning("Invalid LOE schedule date: %s", m[0])
        return None

    updated_at = None
    if u := _UPDATED_RE.search(text):
        try:
            updated_at = datetime(int(u[5]), int(u[4]), int(u[3]), int(u[1]), int(u[2]), tzinfo=KYIV)
        except ValueError:
            pass

    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    matches = list(_GROUP_RE.finditer(text))
    newlines = [i for i, char in enumerate(text) if char == "\n"]
    for i, g in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if (n := bisect.bisect_left(newlines, g.end())) < len(newlines):
            end = min(end, newlines[n])
        group = f"{g[1]}.{g[2]}"
        outages = _outages(day, text[g.end() : end])
        groups.pop(group, None)
        unknown.pop(group, None)
        if outages is None:
            unknown[group] = text[g.start() : end].strip()[:MAX_SENTENCE]
        else:
            groups[group] = outages

    notes = tuple(
        line[:MAX_SENTENCE]
        for line in lines
        if not (_DAY_RE.search(line) or _UPDATED_RE.search(line) or _GROUP_RE.search(line))
    )[:MAX_NOTES]
    return DaySchedule(day, updated_at, groups, unknown, notes, image_url)


def parse_menu(payload: Any) -> dict[date, DaySchedule]:
    """Parse the ``/api/menus?type=photo-grafic`` response into schedules keyed by day."""
    menus = payload.get("hydra:member") if isinstance(payload, dict) else None
    if not isinstance(menus, list):
        fail("LOE: response has no hydra:member list")
    menu = next((m for m in menus if isinstance(m, dict) and m.get("type") == "photo-grafic"), None)
    if menu is None:
        fail("LOE: response has no photo-grafic menu")

    schedules: dict[date, DaySchedule] = {}
    for item in menu.get("menuItems") or []:
        if not isinstance(item, dict):
            continue
        raw_html = item.get("rawHtml") or ""
        if not isinstance(raw_html, str) or not raw_html.strip():
            continue
        schedule = parse_day(raw_html, _image_url(item.get("imageUrl")))
        if schedule is None:
            continue
        previous = schedules.get(schedule.day)
        if previous is None or _newer(schedule, previous):
            schedules[schedule.day] = schedule
    return schedules


def _image_url(path: object) -> str | None:
    return url_on_host(BASE_URL, path)


def _newer(a: DaySchedule, b: DaySchedule) -> bool:
    return b.updated_at is None or (a.updated_at is not None and a.updated_at > b.updated_at)


class Loe(Provider):
    hosts = frozenset({"api.loe.lviv.ua"})
    min_interval = 180
    kind = Kind.API
    page = SCHEDULE_URL + "?page=1&type=photo-grafic"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        params = {
            "page": "1",
            "type": "photo-grafic",
            # Cloudflare caches this endpoint for up to 2 h (s-maxage=7200);
            # a unique query string makes it fetch a fresh copy from the origin.
            "_": str(int(time.time())),
        }
        resp = await http.get(SCHEDULE_URL, params=params, headers={"Accept": "application/ld+json"})
        return await asyncio.to_thread(parse_menu, resp.json())


def parse_cities(payload: Any) -> list[Choice]:
    """``[{"id": 693, "name": "Львів", "otg": {"name": "Львівська"}}, ...]``; the громада tells apart namesakes."""
    choices = []
    for item in entries(payload, "LOE cities"):
        if not isinstance(item, dict) or not (id_ := numeric_id(item.get("id"))) or not (name := clean_label(item.get("name"))):
            continue
        otg = item.get("otg")
        community = clean_label(otg.get("name"), 40) if isinstance(otg, dict) else None
        choices.append(Choice(id_, f"{name} ({community} громада)" if community else name))
    return choices


def parse_streets(payload: Any) -> list[Choice]:
    """``[{"id": 12446, "city": "/api/pw_cities/693", "name": "Наукова"}, ...]``"""
    choices = []
    for item in entries(payload, "LOE streets"):
        if isinstance(item, dict) and (id_ := numeric_id(item.get("id"))) and (name := clean_label(item.get("name"))):
            choices.append(Choice(id_, name))
    return choices


def parse_buildings(payload: Any) -> dict[str, Counter[str]]:
    """Building name → how many consumers there are in each ``chergGpv`` ("3.1", "Не входить")."""
    buildings: dict[str, Counter[str]] = defaultdict(Counter)
    for item in entries(payload, "LOE buildings"):
        if not isinstance(item, dict) or not (name := clean_label(item.get("buildingName"), 30)):
            continue
        if value := clean_label(item.get("chergGpv"), 40):
            buildings[name][value] += 1
        else:
            buildings.setdefault(name, Counter())
    return dict(buildings)


def resolution(values: Counter[str]) -> Resolution:
    """Groups by how many consumers of the building have them, and the other values."""
    groups: Counter[str] = Counter()
    other = []
    for value, count in values.most_common():
        if group := normalize_group(value):
            groups[group] += count
        else:
            other.append(value)
    return Resolution(tuple(g for g, _ in groups.most_common()), tuple(other))


class LoeLookup(AddressLookup):
    hosts = frozenset({"power-api.loe.lviv.ua"})
    request_gap = 2.0
    max_bytes = 8 * 1024 * 1024  # a long street is ~2.3 MB
    steps = (
        Step("Напишіть назву населеного пункту", "Львів"),
        Step("Напишіть назву вулиці", "Наукова"),
        Step("Напишіть номер будинку", "96", browse=True, house=True),
    )

    async def _buildings(self, http: Fetcher, city: str, street: str) -> dict[str, Counter[str]]:
        params = {"pagination": "false", "city.id": require_id(city), "street.id": require_id(street)}
        resp = await http.get(f"{DIRECTORY_URL}/pw_accounts", params=params, headers={"Accept": "application/json"})
        return parse_buildings(resp.json())

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        if len(path) == 2:
            return pick((Choice(b, b) for b in await self._buildings(http, *path)), query)
        if len(search_term(query)) < self.min_query:
            return []
        headers = {"Accept": "application/json"}
        if not path:
            params = {"pagination": "false", "name": search_term(query)}
            resp = await http.get(f"{DIRECTORY_URL}/pw_cities", params=params, headers=headers)
            return pick(parse_cities(resp.json()), query)
        params = {"pagination": "false", "city.id": require_id(path[0]), "name": search_term(query)}
        resp = await http.get(f"{DIRECTORY_URL}/pw_streets", params=params, headers=headers)
        return pick(parse_streets(resp.json()), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != 3:
            raise ValueError("incomplete address")
        city, street, building = path
        values = (await self._buildings(http, city, street)).get(building)
        if values is None:
            raise ValueError("no such building")
        return resolution(values)
