"""Poltavaoblenergo: the schedule table ``POST https://www.poe.pl.ua/customs/newgpv-info.php`` returns.

The request carries only the date (``seldate={"date_in":"18-10-2026"}``, dashes only); the
answer is an HTML fragment with a summary and ``table.turnoff-scheduleui-table``: per
subqueue a row of 48 half-hour cells, ``light_1`` power on, ``light_2`` off, ``light_3``
switching time when power may be off. An empty body means the day is not published yet.

The group of an address: the filials are the options of ``select#input-filial-main`` on the
outages page; ``POST /customs/search-disconnection-cabinet.php`` with
``disconn={"operation_type": "city"|"street"|"building", "filial_id", "city_id", "street_id"}``
lists each next level as ``[{"instance_id", "instance_name"}]``, and
``POST /customs/disconnectinforesult-cabinet.php`` with ``disconn={"cityid", "streerid", "house"}``
(sic) answers ``[{"gp_qnum_1": "1", "gp_qnum_2": "1", ...}]`` — queue 1.1.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from ..http import Fetcher
from ..models import DaySchedule, Outage, Slot, outages_from_slots
from ._address import clean_label, entries, normalize_group, numeric_id, pick, require_id
from ._html import parse_html
from .base import AddressLookup, Choice, Provider, Resolution, Step, fail

URL = "https://www.poe.pl.ua/customs/newgpv-info.php"
FILIALS_URL = "https://www.poe.pl.ua/disconnection/power-outages/"
SEARCH_URL = "https://www.poe.pl.ua/customs/search-disconnection-cabinet.php"
RESULT_URL = "https://www.poe.pl.ua/customs/disconnectinforesult-cabinet.php"
CELLS = {"light_1": Slot.ON, "light_2": Slot.OFF, "light_3": Slot.MAYBE}
_QUEUE_RE = re.compile(r"(\d)\s{0,3}черга")


def parse(fragment: str, day: date) -> DaySchedule | None:
    if not fragment.strip():
        return None
    table = parse_html(fragment).find("table", cls="turnoff-scheduleui-table")
    if table is None:
        fail("Poltava: no schedule table")
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    queue = None
    for row in table.find_all("tr"):
        cells = [c for c in row.children if not isinstance(c, str) and c.tag == "td"]
        if not cells:
            continue
        if "turnoff-scheduleui-table-queue" in cells[0].classes:
            if not (m := _QUEUE_RE.search(cells[0].text())):
                fail(f"Poltava: unexpected queue cell {cells[0].text()[:40]!r}")
            queue, cells = m[1], cells[1:]
        if not cells or "turnoff-scheduleui-table-subqueue" not in cells[0].classes or queue is None:
            continue
        group = f"{queue}.{cells[0].text()}"
        states = [next((CELLS[c] for c in cell.classes if c in CELLS), None) for cell in cells[1:]]
        if len(states) != 48 or None in states:
            unknown[group] = "Нерозпізнаний графік"
        else:
            groups[group] = outages_from_slots(day, states)  # type: ignore[arg-type]
    if not groups and not unknown:
        fail("Poltava: the schedule table has no rows")
    return DaySchedule(day, groups=groups, unknown=unknown)


class Poltava(Provider):
    hosts = frozenset({"www.poe.pl.ua"})
    ukraine_only = True  # times out or answers 403 from GitHub's runners
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        schedules = {}
        for day in (now.date(), now.date() + timedelta(days=1)):
            seldate = json.dumps({"date_in": f"{day:%d-%m-%Y}"})
            resp = await http.post(URL, data={"seldate": seldate})
            if (schedule := parse(resp.text(), day)) is not None:
                schedules[day] = schedule
        return schedules


def parse_filials(page: str) -> list[Choice]:
    select = parse_html(page).find("select", id="input-filial-main")
    if select is None:
        fail("Poltava: no list of filials on the page")
    choices = []
    for option in select.find_all("option"):
        if (id_ := numeric_id(option.attrs.get("value"))) and id_ != "0" and (name := clean_label(option.text())):
            choices.append(Choice(id_, name))
    return choices


def parse_instances(payload: Any, *, by_name: bool = False) -> list[Choice]:
    """``[{"instance_id": 11389, "instance_name": "місто Полтава"}, ...]``.

    Buildings are numbered 1, 2, 3… in the answer, but the result is asked by the name.
    """
    choices = []
    for item in entries(payload, "Poltava addresses"):
        if not isinstance(item, dict) or not (name := clean_label(item.get("instance_name"), 40 if by_name else 80)):
            continue
        if by_name:
            choices.append(Choice(name, name))
        elif id_ := numeric_id(item.get("instance_id")):
            choices.append(Choice(id_, name))
    return choices


def parse_result(payload: Any) -> Resolution:
    items = entries(payload, "Poltava address result")
    item = items[0] if items and isinstance(items[0], dict) else {}
    queue, subqueue = item.get("gp_qnum_1"), item.get("gp_qnum_2")
    group = normalize_group(f"{queue}.{subqueue}") if isinstance(queue, str) and isinstance(subqueue, str) else None
    return Resolution((group,) if group else ())


class PoltavaLookup(AddressLookup):
    hosts = frozenset({"www.poe.pl.ua"})
    steps = (
        Step("Оберіть філію або дільницю Полтаваобленерго", browse=True, in_label=False),
        Step("Напишіть назву населеного пункту", "Полтава", browse=True),
        Step("Напишіть назву вулиці", "Соборності", browse=True),
        Step("Напишіть номер будинку", "10", browse=True, house=True),
    )

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        if not path:
            return pick(parse_filials((await http.get(FILIALS_URL)).text()), query)
        operation = ("city", "street", "building")[len(path) - 1]
        ids = [require_id(p) for p in path] + ["", ""]
        search = {"operation_type": operation, "filial_id": ids[0], "city_id": ids[1], "street_id": ids[2]}
        resp = await http.post(SEARCH_URL, data={"disconn": json.dumps(search)})
        return pick(parse_instances(resp.json(), by_name=operation == "building"), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        _, city, street, house = path
        disconn = {"cityid": require_id(city), "streerid": require_id(street), "house": house}
        resp = await http.post(RESULT_URL, data={"disconn": json.dumps(disconn, ensure_ascii=False)})
        return parse_result(resp.json())
