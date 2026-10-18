"""Chernivtsioblenergo: the letter grid on ``https://oblenergo.cv.ua/shutdowns/`` (``?next`` for tomorrow).

::

    <div id="gsv_t"><div><b>18.10.2026</b></div></div>
    <div id="gsv_a"><p>Графік актуальний станом на <b>17:00</b>...</p></div>
    <div id="inf1" data-id="1"><u>з</u><u>в</u>...48 cells...</div>      groups 1–12

``в`` — power off, ``з`` — on, ``мз`` — may be on. Until tomorrow is published ``?next`` serves
today again, so the date in ``gsv_t`` decides which day a page is.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._html import Node, parse_html
from .base import Provider, fail

URL = "https://oblenergo.cv.ua/shutdowns/"
LETTERS = {"з": Slot.ON, "в": Slot.OFF, "мз": Slot.MAYBE, "мв": Slot.MAYBE}
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_TIME_RE = re.compile(r"станом на\s{0,3}(\d{1,2}):(\d{2})")


def parse(page: str, now: datetime) -> DaySchedule | None:
    root = parse_html(page)
    title = root.find(id="gsv_t")
    if title is None:
        fail("Chernivtsi: no gsv_t date block")
    if not (m := _DATE_RE.search(title.text())):
        return None
    try:
        day = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        fail("Chernivtsi: invalid date")

    updated = None
    if (asof := root.find(id="gsv_a")) is not None and (t := _TIME_RE.search(asof.text())):
        try:
            moment = datetime.combine(now.date(), time(int(t[1]), int(t[2])), KYIV)
            updated = moment if moment <= now else None
        except ValueError:
            pass

    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    for n in range(1, 13):
        row = root.find(id=f"inf{n}")
        if row is None:
            continue
        cells = [c.text().lower() for c in row.children if isinstance(c, Node)]
        states = [LETTERS.get(c) for c in cells]
        if len(states) != 48 or None in states:
            unknown[str(n)] = "Нерозпізнаний графік"
        else:
            groups[str(n)] = outages_from_slots(day, states)  # type: ignore[arg-type]
    if not groups and not unknown:
        fail("Chernivtsi: no group rows")
    return DaySchedule(day, updated, groups, unknown)


class Chernivtsi(Provider):
    hosts = frozenset({"oblenergo.cv.ua"})
    ukraine_only = True  # times out or answers 403 from GitHub's runners
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        schedules = {}
        for url in (URL, URL + "?next"):
            schedule = await asyncio.to_thread(parse, (await http.get(url)).text(), now)
            if schedule is not None and schedule.day >= now.date():
                schedules[schedule.day] = schedule
        return dict(sorted(schedules.items()))
