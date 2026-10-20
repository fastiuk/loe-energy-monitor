"""Chernihivoblenergo: which day a schedule applies and its text changes, from the official channel.

The hours exist only as a picture (``/files/other/schedule_groups.jpg`` on the site, the same
picture in the channel) and in a JSON API behind Cloudflare Turnstile, which is not worked
around. Reading the picture without an OCR engine confuses 0/6/8/9 into believable hours, so it
is not read: a wrong hour is worse than none. What the public preview of the official channel
``https://t.me/s/chernigivoblenergo`` gives in text is used instead:

    За вказівкою НЕК "Укренерго", 17 жовтня в області діятиме Графік погодинних відключень (ГПВ) ☝️.
    За вказівкою НЕК "Укренерго", маємо зміни у Графіку погодинних відключень (ГПВ) на 16 жовтня ☝️.
    🕗 З 17:00 додатково відключається черга 6/2.
    🕗 З 20:00 до 22:00 додатково відключається черга 3/2.

So a day is "published" with every group's hours on the picture (linked) and, for the groups a
later post names, its change lines. A post saying «В оновленому графіку…» brings a new picture:
changes before it no longer apply. Changes reach subscribers as changes of their group.
The site itself drops connections after a few requests and is not contacted.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

from ..http import Fetcher
from ..models import KYIV, DaySchedule
from ._html import Node, parse_html
from ._text import MONTHS
from .base import Kind, Provider, fail

CHANNEL = "chernigivoblenergo"
URL = f"https://t.me/s/{CHANNEL}"
POST_URL = f"https://t.me/{CHANNEL}/{{number}}"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
_NUMBER_RE = re.compile(r"[0-9]{1,12}")
MAX_POST = 5000
MAX_TEXT = 300
ON_PICTURE = "Години — на картинці оператора"

_MONTH = r"(\d{1,2})\s{1,3}(" + "|".join(MONTHS) + r")"
_ANNOUNCED_RE = re.compile(_MONTH + r"\s{1,3}в\s{1,3}області\s{1,3}діятиме\s{1,3}Графік", re.IGNORECASE)
_CHANGED_RE = re.compile(
    r"зміни\s{1,3}у\s{1,3}Графіку\s{1,3}погодинних\s{1,3}відключень[^\n]{0,20}?\s{1,3}на\s{1,3}" + _MONTH,
    re.IGNORECASE,
)
_NEW_PICTURE_RE = re.compile(r"оновленому\s{1,3}графіку", re.IGNORECASE)
_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_GROUP_RE = re.compile(r"(?<![\d/])([1-6])\s{0,2}/\s{0,2}([12])(?![\d/])")


def _lines(node: Node) -> list[str]:
    parts: list[str] = []
    stack: list[Node | str] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif item.tag == "br":
            parts.append("\n")
        else:
            stack.extend(reversed(item.children))
    return [" ".join(line.split()) for line in "".join(parts)[:MAX_POST].split("\n")]


def _day(match: re.Match[str], published: datetime) -> date | None:
    """The day a post means: the one nearest to when it was posted (around New Year too)."""
    # IGNORECASE also matches look-alikes .lower() keeps (U+1C82 for "о"): not a month.
    if (month := MONTHS.get(match[2].lower())) is None:
        return None
    number, around = int(match[1]), published.date()
    for year in (around.year, around.year + 1, around.year - 1):
        try:
            day = date(year, month, number)
        except ValueError:
            return None
        if abs(day - around) <= timedelta(days=31):
            return day
    return None


def _change_lines(lines: list[str]) -> dict[str, list[str]]:
    """Change lines by the groups they name: «З 20:00 до 22:00 додатково відключається черга 3/2»."""
    changes: dict[str, list[str]] = {}
    for line in lines:
        if "черг" not in line.lower() or not _TIME_RE.search(line):
            continue
        text = line.lstrip("🕗 ").rstrip(".")
        for queue, sub in _GROUP_RE.findall(line):
            changes.setdefault(f"{queue}.{sub}", []).append(text)
    return changes


def parse_channel(markup: str) -> dict[date, DaySchedule]:
    """For each day the posts on the page mention: its picture's post and the changes after it."""
    root = parse_html(markup)
    if root.find(cls="tgme_channel_history") is None:
        fail("Chernihiv: not a Telegram channel preview")
    posts = []
    for post in root.find_all("div", cls="tgme_widget_message"):
        number = post.attrs.get("data-post", "").rpartition("/")[2]
        body, moment = post.find("div", cls="tgme_widget_message_text"), post.find("time")
        if not _NUMBER_RE.fullmatch(number) or body is None or moment is None:
            continue
        try:
            published = datetime.fromisoformat(moment.attrs.get("datetime", "")).astimezone(KYIV)
        except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
            continue
        posts.append((int(number), published, _lines(body)))

    # Oldest first, so that a later picture drops the changes made before it.
    pictures: dict[date, tuple[int, datetime]] = {}
    changes: dict[date, dict[str, list[str]]] = {}
    updated: dict[date, datetime] = {}
    for number, published, lines in sorted(posts):
        text = "\n".join(lines)
        if (m := _ANNOUNCED_RE.search(text)) and (day := _day(m, published)):
            pictures[day], changes[day], updated[day] = (number, published), {}, published
        elif (m := _CHANGED_RE.search(text)) and (day := _day(m, published)):
            if _NEW_PICTURE_RE.search(text):
                pictures[day], changes[day] = (number, published), {}
            for group, found in _change_lines(lines).items():
                changes.setdefault(day, {}).setdefault(group, []).extend(found)
            updated[day] = published

    schedules = {}
    for day in sorted(updated):
        unknown = {}
        for group in GROUPS:
            text = ON_PICTURE
            if found := changes.get(day, {}).get(group):
                text += "; зміни: " + "; ".join(found)
            unknown[group] = text[:MAX_TEXT]
        picture = pictures.get(day)
        image_url = POST_URL.format(number=picture[0]) if picture else None
        schedules[day] = DaySchedule(day, updated[day], {}, unknown, image_url=image_url)
    return schedules


class Chernihiv(Provider):
    hosts = frozenset({"t.me"})
    min_interval = 300
    hours_on_picture = True
    kind = Kind.TELEGRAM
    page = URL

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        schedules = await asyncio.to_thread(parse_channel, (await http.get(URL)).text())
        return {day: s for day, s in schedules.items() if today <= day <= today + timedelta(days=1)}
