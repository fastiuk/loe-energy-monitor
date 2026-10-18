"""Cherkasyoblenergo: schedule posts in the site's CMS API.

::

    GET https://www.cherkasyoblenergo.com/api/v1/posts/main?lang=uk&limit=200
        → {"data": [{"title": "Оновлено графік погодинних відключень (ГПВ) на 18 жовтня",
                     "slug": "onovleno-...-na-18-zhovtnya-3", "publishedAt": "2026-10-18T14:49:00"}, ...]}
    GET https://www.cherkasyoblenergo.com/api/v1/posts/{slug}?lang=uk
        → {"data": {"content": "<p>1.1 10<span>:00 - 12:00, </span>...</p>", ...}}

The newest post for a day wins. ``publishedAt`` is Kyiv time; titles carry no year.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule
from ._text import find_day, html_lines, parse_group_text
from .base import Provider, fail

BASE_URL = "https://www.cherkasyoblenergo.com/api/v1/posts"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
_TITLE_RE = re.compile(r"графік\w{0,2}\s{1,3}погодинних\s{1,3}відключень", re.IGNORECASE)
_NOT_APPLIED_RE = re.compile(r"не\s{1,3}(?:застосов|діят|плану|будуть)", re.IGNORECASE)
_SLUG_RE = re.compile(r"[a-z0-9-]{1,200}")


def pick_posts(payload: Any, today: date) -> dict[date, tuple[str, datetime]]:
    """The newest schedule post (slug, publication time) for today and later days."""
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        fail("Cherkasy: posts response has no data list")
    posts: dict[date, tuple[str, datetime]] = {}
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        title, slug = str(item.get("title"))[:300], str(item.get("slug"))
        if not _TITLE_RE.search(title) or not _SLUG_RE.fullmatch(slug):
            continue
        try:
            published = datetime.fromisoformat(str(item.get("publishedAt"))).replace(tzinfo=KYIV)
        except ValueError:
            continue
        day = find_day(title, near=published.date())
        if day is None or day < today:
            continue
        if day not in posts or published > posts[day][1]:
            posts[day] = (slug, published)
    return posts


def parse_post(payload: Any, day: date, published: datetime) -> DaySchedule:
    data = payload.get("data") if isinstance(payload, dict) else None
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str):
        fail("Cherkasy: post has no content")
    text = "\n".join(html_lines(content))
    groups, unknown = parse_group_text(day, text)
    if not groups and not unknown and _NOT_APPLIED_RE.search(text):
        groups = dict.fromkeys(GROUPS, ())
    return DaySchedule(day, published, groups, unknown)


class Cherkasy(Provider):
    hosts = frozenset({"www.cherkasyoblenergo.com"})
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        posts = await http.get(f"{BASE_URL}/main", params={"lang": "uk", "limit": "200"})
        schedules = {}
        for day, (slug, published) in sorted(pick_posts(posts.json(), now.date()).items()):
            post = await http.get(f"{BASE_URL}/{slug}", params={"lang": "uk"})
            schedules[day] = parse_post(post.json(), day, published)
        return schedules
