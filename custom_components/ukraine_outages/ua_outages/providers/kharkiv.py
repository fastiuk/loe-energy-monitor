"""Kharkivoblenergo: schedule news posts, read from the RSS feed that carries their full text.

``GET https://oblenergo.kharkiv.ua/uk/rss.xml``; a schedule item is titled "У Харківській
області 17 жовтня діятимуть графіки погодинних відключень" and its body lists
``1.1 07:00-10:30; 17:30-21:00``. Posts go out the evening before; the newest per day wins.
"""

from __future__ import annotations

import asyncio
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
MAX_ITEM = 200_000
_SCHEDULE_RE = re.compile(r"графік\w{0,2}\s{1,3}погодинних\s{1,3}відключень", re.IGNORECASE)
_NOT_APPLIED_RE = re.compile(r"не\s{1,3}(?:застосов|діят|плану|будуть)", re.IGNORECASE)


def _elements(text: str, tag: str, limit: int) -> list[str]:
    """Contents of every ``<tag>…</tag>``, found in one pass with ``str.find`` (no regex retries)."""
    found, start, opening, closing = [], 0, f"<{tag}>", f"</{tag}>"
    while (begin := text.find(opening, start)) >= 0:
        begin += len(opening)
        if (end := text.find(closing, begin)) < 0:
            break
        if end - begin <= limit:
            found.append(text[begin:end])
        start = end + len(closing)
    return found


def _first(text: str, tag: str, limit: int) -> str:
    return next(iter(_elements(text, tag, limit)), "")


def parse_feed(feed: str, today: date) -> dict[date, DaySchedule]:
    if "<rss" not in feed[:1000]:
        fail("Kharkiv: not an RSS feed")
    best: dict[date, DaySchedule] = {}
    for item in _elements(feed, "item", MAX_ITEM):
        title = html.unescape(_first(item, "title", 1000).strip())
        if not _SCHEDULE_RE.search(title) or "ГОП" in title:
            continue
        try:
            stamp = _first(item, "pubDate", 100).strip()
            published = parsedate_to_datetime(stamp).astimezone(KYIV) if stamp else None
        except (TypeError, ValueError):
            published = None
        day = find_day(title, near=published.date() if published else today)
        if day is None or day < today:
            continue
        body = html.unescape(_first(item, "description", MAX_ITEM))
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
        return await asyncio.to_thread(parse_feed, resp.text(), now.date())
