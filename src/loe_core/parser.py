"""Parse the text schedule LOE publishes in ``rawHtml`` of the ``photo-grafic`` menu.

Known sentence formats::

    Група 1.1. Електроенергії немає з 20:00 до 22:30.
    Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 22:30.
    Група 2.1. Електроенергії немає з 08:00 до 10:30 з 14:00 до 17:00.
    Група 2.2. Електроенергії немає з 23:00 до 24:00.
    Група 3.1. Електроенергії немає з 21:00 до 02:00.
    Група 1.1. Електроенергія є.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from .models import KYIV, DaySchedule, Outage, merge_outages

log = logging.getLogger(__name__)

BASE_URL = "https://api.loe.lviv.ua"

_BLOCK_TAG_RE = re.compile(r"<\s*/?\s*(?:p|div|br|li|ul|ol|tr|h\d)\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
_SPACE_RE = re.compile(r"[ \t ]+")
_DAY_RE = re.compile(r"графік\b.*?\bна\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE)
_UPDATED_RE = re.compile(
    r"станом\s+на\s+(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", re.IGNORECASE
)
_GROUP_RE = re.compile(r"Груп[аи]\s+(\d)\s*\.\s*(\d)\.?", re.IGNORECASE)
_INTERVAL_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(?:до|-|–|—)\s*(\d{1,2}):(\d{2})")
_POWER_ON_RE = re.compile(r"електроенергія\s+є|відключень\s+не", re.IGNORECASE)


def _text_lines(raw_html: str) -> list[str]:
    text = _BLOCK_TAG_RE.sub("\n", raw_html)
    text = html.unescape(_TAG_RE.sub("", text))
    lines = (_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    return [line for line in lines if line]


def _at(day: date, hour: int, minute: int) -> datetime:
    # LOE writes the end of the day as 24:00.
    extra_days, hour = divmod(hour, 24)
    return datetime.combine(day + timedelta(days=extra_days), time(hour, minute), KYIV)


def _parse_outages(day: date, sentence: str) -> tuple[Outage, ...] | None:
    """Outages from one group sentence; ``()`` when power is on, ``None`` if unrecognised."""
    outages = []
    try:
        for h1, m1, h2, m2 in _INTERVAL_RE.findall(sentence):
            start = _at(day, int(h1), int(m1))
            end = _at(day, int(h2), int(m2))
            if end <= start:  # "з 21:00 до 02:00" runs past midnight
                end += timedelta(days=1)
            outages.append(Outage(start, end))
    except ValueError:
        return None
    if outages:
        return merge_outages(outages)
    if _POWER_ON_RE.search(sentence):
        return ()
    return None


def parse_day(raw_html: str, image_url: str | None = None) -> DaySchedule | None:
    """Parse one day's ``rawHtml``; ``None`` if it is empty or has no date header."""
    lines = _text_lines(raw_html or "")
    text = "\n".join(lines)
    if not (m := _DAY_RE.search(text)):
        if text:
            log.warning("Schedule text has no date header: %.200s", text)
        return None
    try:
        day = date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        log.warning("Invalid schedule date: %s", m[0])
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
    for i, g in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if (newline := text.find("\n", g.end())) != -1:
            end = min(end, newline)
        group = f"{g[1]}.{g[2]}"
        outages = _parse_outages(day, text[g.end() : end])
        groups.pop(group, None)
        unknown.pop(group, None)
        if outages is None:
            sentence = text[g.start() : end].strip()
            log.warning("Unrecognised schedule sentence: %s", sentence)
            unknown[group] = sentence
        else:
            groups[group] = outages

    notes = tuple(
        line
        for line in lines
        if not (_DAY_RE.search(line) or _UPDATED_RE.search(line) or _GROUP_RE.search(line))
    )
    return DaySchedule(day, updated_at, groups, unknown, notes, image_url)


def parse_menu(payload: dict[str, Any]) -> dict[date, DaySchedule]:
    """Parse the ``/api/menus?type=photo-grafic`` response into schedules keyed by day.

    The menu holds "Today", "Arhiv" and "Tomorrow" items; only items with ``rawHtml`` count.
    Raises ``ValueError`` if the response does not look like the schedule menu at all.
    """
    menus = payload.get("hydra:member")
    if not isinstance(menus, list):
        raise ValueError("Response has no hydra:member list")
    menu = next((m for m in menus if m.get("type") == "photo-grafic"), None)
    if menu is None:
        raise ValueError("Response has no photo-grafic menu")

    schedules: dict[date, DaySchedule] = {}
    for item in menu.get("menuItems") or []:
        raw_html = item.get("rawHtml") or ""
        if not raw_html.strip():
            continue
        image = item.get("imageUrl") or ""
        schedule = parse_day(raw_html, BASE_URL + image if len(image) > 1 else None)
        if schedule is None:
            continue
        previous = schedules.get(schedule.day)
        if previous is None or _newer(schedule, previous):
            schedules[schedule.day] = schedule
    return schedules


def _newer(a: DaySchedule, b: DaySchedule) -> bool:
    return b.updated_at is None or (a.updated_at is not None and a.updated_at > b.updated_at)
