"""Kharkivoblenergo: schedule news posts, read from the RSS feed that carries their full text.

``GET https://oblenergo.kharkiv.ua/uk/rss.xml``; a schedule item is titled "У Харківській
області 17 жовтня діятимуть графіки погодинних відключень" and its body lists
``1.1 07:00-10:30; 17:30-21:00``. Posts go out the evening before; the newest per day wins.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from ..http import Fetcher
from ..models import KYIV, DaySchedule
from ._text import find_day, html_lines, parse_group_text
from .base import Provider, fail

URL = "https://oblenergo.kharkiv.ua/uk/rss.xml"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
_ITEM_RE = re.compile(r"<item>(.{0,200000}?)</item>", re.DOTALL)
_TITLE_RE = re.compile(r"<title>(.{0,1000}?)</title>", re.DOTALL)
_DESCRIPTION_RE = re.compile(r"<description>(.{0,200000}?)</description>", re.DOTALL)
_PUBDATE_RE = re.compile(r"<pubDate>(.{0,100}?)</pubDate>", re.DOTALL)
_SCHEDULE_RE = re.compile(r"графік\w{0,2}\s{1,3}погодинних\s{1,3}відключень", re.IGNORECASE)
_NOT_APPLIED_RE = re.compile(r"не\s{1,3}(?:застосов|діят|плану|будуть)", re.IGNORECASE)


def parse_feed(feed: str, today: date) -> dict[date, DaySchedule]:
    if "<rss" not in feed[:1000]:
        fail("Kharkiv: not an RSS feed")
    best: dict[date, DaySchedule] = {}
    for item in _ITEM_RE.findall(feed):
        title = html.unescape((m[1] if (m := _TITLE_RE.search(item)) else "").strip())
        if not _SCHEDULE_RE.search(title) or "ГОП" in title:
            continue
        try:
            published = parsedate_to_datetime(m[1].strip()).astimezone(KYIV) if (m := _PUBDATE_RE.search(item)) else None
        except (TypeError, ValueError):
            published = None
        day = find_day(title, near=published.date() if published else today)
        if day is None or day < today:
            continue
        body = html.unescape(m[1]) if (m := _DESCRIPTION_RE.search(item)) else ""
        text = "\n".join(html_lines(body))
        groups, unknown = parse_group_text(day, text)
        if not groups and not unknown:
            if not _NOT_APPLIED_RE.search(title):
                continue
            groups = dict.fromkeys(GROUPS, ())
        schedule = DaySchedule(day, published, groups, unknown)
        current = best.get(day)
        if current is None or (published and (current.updated_at is None or published > current.updated_at)):
            best[day] = schedule
    return dict(sorted(best.items()))


class Kharkiv(Provider):
    hosts = frozenset({"oblenergo.kharkiv.ua"})
    ukraine_only = True  # times out or answers 403 from GitHub's runners
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL, headers={"Accept": "application/rss+xml, application/xml"})
        return parse_feed(resp.text(), now.date())
