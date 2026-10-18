"""Rivneoblenergo: the table in ``div#fetched-data-container`` on ``https://www.roe.vsei.ua/disconnections``.

A header row lists the subqueues (``1.1`` … ``6.2``); then one row per day: the date cell
("18.10.2026") and a cell per subqueue with ``<p>HH:MM-HH:MM</p>`` lines or "Очікується"
(not published yet). The page footer says "Оновлено: 18.10.2026 16:30".
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage
from ._html import Node, parse_html
from ._text import NO_OUTAGES_RE, parse_intervals
from .base import Provider, fail

URL = "https://www.roe.vsei.ua/disconnections"
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_GROUP_RE = re.compile(r"\d\.\d")
_UPDATED_RE = re.compile(r"Оновлено:\s{0,3}(\d{2})\.(\d{2})\.(\d{4})\s{1,3}(\d{2}):(\d{2})")
_PENDING = "очікується"
_EMPTY = {"", "-", "–", "—"}


def _cells(row: Node) -> list[Node]:
    return [c for c in row.children if isinstance(c, Node) and c.tag in ("td", "th")]


def parse(page: str) -> dict[date, DaySchedule]:
    root = parse_html(page)
    container = root.find(id="fetched-data-container")
    if container is None:
        fail("Rivne: no fetched-data-container")
    updated = None
    if m := _UPDATED_RE.search(root.text()):
        try:
            updated = datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4]), int(m[5]), tzinfo=KYIV)
        except ValueError:
            pass

    header: list[str] = []
    schedules = {}
    for row in container.find_all("tr"):
        texts = [c.text() for c in _cells(row)]
        labels = [t for t in texts if _GROUP_RE.fullmatch(t)]
        if len(labels) >= 6:
            header = labels
            continue
        if not texts or not (m := _DATE_RE.fullmatch(texts[0])) or not header:
            continue
        try:
            day = date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            fail(f"Rivne: invalid date {texts[0]!r}")
        cells = texts[1:]
        if len(cells) != len(header):
            fail(f"Rivne: {len(cells)} cells for {len(header)} groups")
        groups: dict[str, tuple[Outage, ...]] = {}
        unknown: dict[str, str] = {}
        for group, text in zip(header, cells):
            if text.lower() == _PENDING:
                continue
            outages = parse_intervals(day, text)
            if outages is None and (text in _EMPTY or NO_OUTAGES_RE.search(text)):
                outages = ()
            if outages is None:
                unknown[group] = text[:300]
            else:
                groups[group] = outages
        if groups or unknown:
            schedules[day] = DaySchedule(day, updated, groups, unknown)
    if not header:
        fail("Rivne: no group header in the table")
    return dict(sorted(schedules.items()))


class Rivne(Provider):
    hosts = frozenset({"www.roe.vsei.ua"})
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL)
        return parse(resp.text())
