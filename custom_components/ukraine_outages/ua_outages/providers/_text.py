"""Helpers for schedules published as text: "1.1 07:00-10:30; 17:30-21:00".

Every pattern uses bounded quantifiers so that parsing stays linear in the input size.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

from ..models import Outage, at, merge_outages

#: A real day's text is a few KB; anything far bigger is not a schedule.
MAX_TEXT = 200_000
MAX_NOTE = 300

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}  # fmt: skip

# Bounded whitespace: two unbounded \s* around the optional "/" made "<" plus many spaces quadratic.
_BLOCK_TAG_RE = re.compile(
    r"<\s{0,5}/?\s{0,5}(?:p|div|br|li|ul|ol|tr|td|th|table|h\d|article|section)\b[^<>]{0,500}>", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^<>]{0,1000}>")
_SPACE_RE = re.compile(r"[ \t  ]+")
INTERVAL_RE = re.compile(
    r"(?<!\d)(\d{1,2})[:.](\d{2})\s{0,3}(?:-|–|—|до|по)\s{0,3}(\d{1,2})[:.](\d{2})(?!\d)"
)
_DAY_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s{1,3}(" + "|".join(MONTHS) + r")", re.IGNORECASE)


def html_lines(markup: str) -> list[str]:
    """Visible text of an HTML fragment, one non-empty line per block element."""
    text = _BLOCK_TAG_RE.sub("\n", markup[:MAX_TEXT])
    text = html.unescape(_TAG_RE.sub("", text))
    lines = (_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    return [line for line in lines if line]


def plain_text(markup: str) -> str:
    """Text of an inline fragment where tags may split words ("<span>16</span><span>:00</span>")."""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub("", markup[:MAX_TEXT]))).strip()


def parse_intervals(day: date, text: str) -> tuple[Outage, ...] | None:
    """Outages from "07:00-10:30; 17:30-21:00"; ``None`` if there is no valid interval.

    "24:00" and an end of "00:00" both mean midnight; "21:00-02:00" runs past midnight.
    """
    outages = []
    for h1, m1, h2, m2 in INTERVAL_RE.findall(text):
        h1, m1, h2, m2 = int(h1), int(m1), int(h2), int(m2)
        if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
            return None
        start, end = at(day, h1, m1), at(day, h2, m2)
        if end <= start:
            end += timedelta(days=1)
        if end - start > timedelta(hours=24):
            return None
        outages.append(Outage(start, end))
    return merge_outages(outages) if outages else None


def find_day(text: str, *, near: date) -> date | None:
    """The first "17 жовтня" in ``text`` as a date, picking the year closest to ``near``."""
    if not (m := _DAY_MONTH_RE.search(text)):
        return None
    day, month = int(m[1]), MONTHS[m[2].lower()]
    candidates = []
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs(d - near), default=None)


#: "1.1", "1.1:", "1/1" — not part of a date ("17.10") or a time ("10.30").
GROUP_MARK_RE = re.compile(r"(?<![\d:.,/])([1-6])\s{0,2}[./]\s{0,2}([12])(?!\d)\s{0,2}[:.)]?")
NO_OUTAGES_RE = re.compile(
    r"світло\s{1,3}є|електроенергія\s{1,3}є|без\s{1,3}відключень|відключень\s{1,3}(?:не|немає)|не\s{1,3}відключа",
    re.IGNORECASE,
)


def parse_group_text(
    day: date, text: str
) -> tuple[dict[str, tuple[Outage, ...]], dict[str, str]]:
    """Groups and their outages from "1.1 07:00-10:30; 17:30-21:00\\n1.2: 09:30 – 13:30 …".

    A group's text runs to the next group or the end of its line. Returns parsed groups and
    the raw text of groups that could not be parsed.
    """
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    marks = list(GROUP_MARK_RE.finditer(text[:MAX_TEXT]))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        if (newline := text.find("\n", mark.end(), end)) >= 0:
            end = newline
        group, segment = f"{mark[1]}.{mark[2]}", text[mark.end() : end]
        outages = parse_intervals(day, segment)
        if outages is None and NO_OUTAGES_RE.search(segment):
            outages = ()
        groups.pop(group, None)
        unknown.pop(group, None)
        if outages is None:
            unknown[group] = text[mark.start() : end].strip()[:MAX_NOTE]
        else:
            groups[group] = outages
    return groups, unknown


def normalize_group(text: str) -> str:
    """ "1/1", "1 . 1" and "1.1" all become "1.1"."""
    return re.sub(r"\s{0,3}[./]\s{0,3}", ".", text.strip())
