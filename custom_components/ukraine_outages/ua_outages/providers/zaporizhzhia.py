"""Zaporizhzhiaoblenergo: schedule articles on ``https://www.zoe.com.ua/outage/``.

Each ``<article>`` has an ``<h2>`` title and a body:

    ОНОВЛЕНО ГПВ НА 17 ЖОВТНЯ ПО ЗАПОРІЗЬКІЙ ОБЛАСТІ (оновлено 18:52)
        1.1: 10:30 – 13:30, 17:00 – 18:30
        1.2: 09:30 – 13:30 ...
    17 ЖОВТНЯ ПО ЗАПОРІЗЬКІЙ ОБЛАСТІ ДІЯТИМУТЬ ГПВ
    ГРАФІКИ ПОГОДИННИХ ВІДКЛЮЧЕНЬ НА 06 жовтня НЕ ЗАПЛАНОВАНІ
    ДО УВАГИ ПРОМИСЛОВОСТІ ТА БІЗНЕСУ 18 ЖОВТНЯ            (power limits for business: skipped)

Articles are listed newest first, so the first one for a day wins. "Решта черг … не задіяні"
means the groups not listed have no outages.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..http import Fetcher
from ..models import DaySchedule
from ._html import parse_html
from ._text import find_day, parse_group_text
from .base import Provider, fail

URL = "https://www.zoe.com.ua/outage/"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
_SCHEDULE_RE = re.compile(r"ГПВ|погодинних\s{1,3}відключень", re.IGNORECASE)
_BUSINESS_RE = re.compile(r"промисловост|бізнес|ГОП|обмеження\s{1,3}потужност", re.IGNORECASE)
_NOT_PLANNED_RE = re.compile(r"не\s{1,3}(?:заплановані|застосов|діят)", re.IGNORECASE)
_REST_OFF_RE = re.compile(r"решта\s{1,3}черг.{0,80}?не\s{1,3}задіян", re.IGNORECASE | re.DOTALL)


def parse(page: str, today: date) -> dict[date, DaySchedule]:
    root = parse_html(page)
    articles = root.find_all("article")
    if not articles:
        fail("Zaporizhzhia: no articles on the outage page")
    schedules: dict[date, DaySchedule] = {}
    for article in articles:
        heading = article.find("h2")
        title = heading.text() if heading else ""
        if not _SCHEDULE_RE.search(title) or _BUSINESS_RE.search(title):
            continue
        day = find_day(title, near=today)
        if day is None or day in schedules or not today <= day <= today + timedelta(days=1):
            continue
        if _NOT_PLANNED_RE.search(title):
            schedules[day] = DaySchedule(day, groups=dict.fromkeys(GROUPS, ()))
            continue
        content = article.find(cls="content") or article
        lines = []
        for block in content.find_all("p") or [content]:
            # <br> separates the group lines inside one paragraph.
            parts: list[str] = [""]
            for child in block.children:
                if isinstance(child, str):
                    parts[-1] += child
                elif child.tag == "br":
                    parts.append("")
                else:
                    parts[-1] += child.text()
            lines += [p.strip() for p in parts if p.strip()]
        text = "\n".join(lines)
        groups, unknown = parse_group_text(day, text)
        if not groups and not unknown:
            continue
        if _REST_OFF_RE.search(text):
            groups = {**dict.fromkeys((g for g in GROUPS if g not in unknown), ()), **groups}
        schedules[day] = DaySchedule(day, groups=groups, unknown=unknown)
    return dict(sorted(schedules.items()))


class Zaporizhzhia(Provider):
    hosts = frozenset({"www.zoe.com.ua"})
    ukraine_only = True  # times out or answers 403 from GitHub's runners
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL)
        return parse(resp.text(), now.date())
