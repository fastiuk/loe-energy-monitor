"""Kirovohradoblenergo: the oblast's schedule as text in its official district Telegram channels.

The site's own JSON (``kiroe.com.ua/electricity-blackout/actual-gpv-graphs``) answers only
browser-like clients past a Cloudflare challenge, so it is not used. Every official district
channel posts the same text within a second of the dispatcher entering a schedule, and the
public web preview ``https://t.me/s/<channel>`` shows the latest ~20 posts::

    ⚡ Зміни на 17:25 01.07.2026 до графіка погодинних відключень
    За розпорядженням НЕК "Укренерго" 01.07.2026 внесено зміни до графіка погодинних відключень (ГПВ):
    Черга 1.1: -
    Черга 1.2: 20-22
    Черга 6.1: 19:00-20:30, 22:00-24:00

- The day is the one in «Укренерго" DD.MM.YYYY буде діяти / буде застосовано / внесено зміни».
- Each post is the whole schedule for its day: groups it leaves out (as posts do since
  October 2026) have no outages. The newest post for a day wins.
- «… DD.MM.YYYY застосування графіка … скасовано»: no outages that day.
- Forecasts («Попередній прогноз …», «Прогнозовано …») are not schedules: skipped.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, at, merge_outages
from ._html import Node, parse_html
from .base import Kind, Provider, fail

URL = "https://t.me/s/{channel}"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
#: The city channel has the least local news; a district channel posts the same text.
CHANNELS = ("SvitloKropyvnytskyiMisto", "SvitloOleksandriyskohoRaionu")
_CHANNEL_RE = re.compile(r"[A-Za-z0-9_]{5,32}")
_NUMBER_RE = re.compile(r"[0-9]{1,12}")
MAX_POST = 5000
#: A post is for the day it is published or the next; further away it is not a schedule.
MAX_LEAD = timedelta(days=3)

_DAY_RE = re.compile(
    r"Укренерго\W{0,3}\s{0,3}(\d{2})\.(\d{2})\.(\d{4})\s{1,3}(буде|внесено|застосування)", re.IGNORECASE
)
_CANCELLED_RE = re.compile(r"застосування\s{1,3}графіка[^\n]{0,80}скасовано", re.IGNORECASE)
_FORECAST_RE = re.compile(r"прогноз", re.IGNORECASE)
_GROUP_LINE_RE = re.compile(r"Черга\s{1,3}([1-6])\s{0,2}\.\s{0,2}([12])\s{0,3}:\s{0,3}(.{0,300})")
_SPAN_RE = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s{0,3}[-–—]\s{0,3}(\d{1,2})(?::(\d{2}))?(?![\d:])")
_NONE_RE = re.compile(r"\s{0,3}[-–—]?\s{0,3}")
UNRECOGNISED = "Нерозпізнаний графік"


def _lines(node: Node) -> list[str]:
    """The text of a post, one line per ``<br>``."""
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
    return [line.strip() for line in "".join(parts)[:MAX_POST].split("\n")]


def _outages(day: date, text: str) -> tuple[Outage, ...] | None:
    """Outages from "16-18", "09:30-10:00, 22:00-24:00" or "-" (none); None if unreadable."""
    if _NONE_RE.fullmatch(text):
        return ()
    outages = []
    for h1, m1, h2, m2 in _SPAN_RE.findall(text):
        h1, m1, h2, m2 = int(h1), int(m1 or 0), int(h2), int(m2 or 0)
        if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59 or (h1, m1) >= (h2, m2) and (h2, m2) != (0, 0):
            return None
        end = at(day, h2, m2) if (h2, m2) != (0, 0) else at(day, 24, 0)
        outages.append(Outage(at(day, h1, m1), end))
    leftover = _SPAN_RE.sub("", text)
    if not outages or leftover.strip(" ,;.") != "":
        return None
    return merge_outages(outages)


def parse_post(lines: list[str], published: datetime) -> DaySchedule | None:
    """The schedule a post announces, or None if it announces none (news, forecasts)."""
    text = "\n".join(lines)
    if (m := _DAY_RE.search(text)) is None or _FORECAST_RE.search(text):
        return None
    try:
        day = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    # 31.12.9999 has no midnight to end an outage at.
    if abs(day - published.date()) > MAX_LEAD or day == date.max:
        return None
    groups: dict[str, tuple[Outage, ...]] = dict.fromkeys(GROUPS, ())
    unknown: dict[str, str] = {}
    if not _CANCELLED_RE.search(text):
        for line in lines:
            if (g := _GROUP_LINE_RE.fullmatch(line)) is None:
                continue
            group = f"{g[1]}.{g[2]}"
            if (outages := _outages(day, g[3])) is None:
                groups.pop(group, None)
                unknown[group] = UNRECOGNISED
            else:
                groups[group] = outages
    return DaySchedule(day, published, groups, unknown)


def parse_channel(markup: str) -> dict[date, DaySchedule]:
    """The newest schedule per day among the posts on a channel's preview page."""
    root = parse_html(markup)
    if root.find(cls="tgme_channel_history") is None:
        fail("Kirovohrad: not a Telegram channel preview")
    newest: dict[date, tuple[int, DaySchedule]] = {}
    for post in root.find_all("div", cls="tgme_widget_message"):
        number = post.attrs.get("data-post", "").rpartition("/")[2]
        body, moment = post.find("div", cls="tgme_widget_message_text"), post.find("time")
        if not _NUMBER_RE.fullmatch(number) or body is None or moment is None:
            continue
        try:
            published = datetime.fromisoformat(moment.attrs.get("datetime", "")).astimezone(KYIV)
        except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
            continue
        schedule = parse_post(_lines(body), published)
        if schedule is not None and (schedule.day not in newest or newest[schedule.day][0] < int(number)):
            newest[schedule.day] = (int(number), schedule)
    return {day: schedule for day, (_, schedule) in sorted(newest.items())}


class Kirovohrad(Provider):
    hosts = frozenset({"t.me"})
    min_interval = 300
    kind = Kind.TELEGRAM

    def __init__(self, channel: str) -> None:
        if not _CHANNEL_RE.fullmatch(channel):
            raise ValueError(f"not a Telegram channel name: {channel!r}")
        self.channel = channel

    @property
    def link(self) -> str:
        return URL.format(channel=self.channel)

    @property
    def name(self) -> str:
        return f"Telegram {self.channel}"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        resp = await http.get(URL.format(channel=self.channel))
        schedules = await asyncio.to_thread(parse_channel, resp.text())
        return {day: s for day, s in schedules.items() if today <= day <= today + timedelta(days=1)}
