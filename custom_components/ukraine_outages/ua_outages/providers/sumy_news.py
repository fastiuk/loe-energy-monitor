"""Sumyoblenergo: the PNG table in each ГПВ news post on ``www.soe.com.ua``.

The operator's JSON API is open only to its own site (see ``sumy.py``), and ``robots.txt``
disallows ``/api/``, so this source reads only pages robots.txt allows:

- ``/press-service/news`` is rendered on the server: the Next.js data in its scripts holds
  ``newsItems`` (six per page, ``?page=2`` for more), sorted by ``date`` (the day a post is
  for), newest first::

      {"id": 445, "categories": [{"name": "ГПВ"}], "title": "Графіки погодинних відключень на 20-10-2026",
       "content": {"content": "$2a"}, "createdAt": "2026-10-19T16:34:40.556Z", "date": "2026-10-20",
       "url": "445/gpv-tomorrow-19-10-2026-19-34"}

  ``content`` is HTML with ``<img src="https://www.soe.com.ua/uploads/public/news/<md5>.png">``,
  or a reference to a text row of the same data (``2a:T868,<html>``, 0x868 bytes long).
  Changes during the day are new posts («Зміни в застосуванні ГПВ на Сумщині», slug
  ``gpv-18-10-2026-21-57`` with the posting time) with a full new table for that day.
- Each upload gets a new file name, so a known URL is an unchanged image.

The PNG is 780 px wide (plus 272 px per box beyond two in the longest row; 1596 px for five)
and 1400–1862 px high: a header «ГПВ на 20.10.2026» and a table with a row per subqueue
1.1–6.2, each either «—» (no outages) or a box per outage, grey #F1F1F1 or yellow #FFF9CD
(the colour only tells x.1 from x.2), with «08:00 - 12:00» in black and «4 год. 00 хв.» in
#666 under it. Digits are read without an OCR engine: rows and boxes are found by colour,
each line of text is cut into glyphs at blank columns, and every glyph is compared with
templates cut from real images (``TEMPLATES``). The fonts are fixed and digits are tabular,
so a glyph is one of a few bitmaps. A group is read only if every glyph is recognised, its
label reads as that group, every interval agrees with the length printed under it and the
intervals follow each other; otherwise it is unknown. The header date must be the day of the
post, or the image is refused.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from functools import cache
from typing import Any, NamedTuple

from PIL import Image, ImageChops

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, at_minute, merge_outages
from ._image import decode_png
from .base import Kind, Provider, SourceFormatError, fail

log = logging.getLogger(__name__)

BASE_URL = "https://www.soe.com.ua"
NEWS_URL = BASE_URL + "/press-service/news"
IMAGE_URL = BASE_URL + "/uploads/public/news/{}.png"
CATEGORY = "ГПВ"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
UNRECOGNISED = "Нерозпізнаний графік"
NO_IMAGE = "В останньому дописі оператора про графіки на цей день немає таблиці: {}"

# --- Finding the posts -------------------------------------------------------------------

_PUSH = "self.__next_f.push("
_PUSH_END = ")</script>"
#: News items read from one page; a page has six.
MAX_ITEMS = 50
#: Rows of the Next.js data; a real page has about 50.
MAX_ROWS = 5_000
_ROW_ID_RE = re.compile(rb"[0-9a-f]{0,8}")
_NEWS_ITEMS_RE = re.compile(r'"newsItems"\s{0,5}:\s{0,5}')
_REF_RE = re.compile(r"\$([0-9a-f]{1,8})")
_SLUG_RE = re.compile(r"\d{1,7}/gpv-[a-z0-9-]{1,80}")
_ISO_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DAY_RE = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{4})(?!\d)")
_IMAGE_RE = re.compile(r"(?:https://www\.soe\.com\.ua)?/uploads/public/news/([0-9a-f]{32})\.png")
#: Text of a post searched for its image and day; a real post has 2 KB.
MAX_CONTENT = 100_000


class Post(NamedTuple):
    id: int
    #: Address of the post on the site.
    url: str
    #: The day the schedule is for.
    day: date
    #: When the post was published, Kyiv time.
    posted: datetime
    #: The first PNG in the post, ``None`` if there is none.
    image_url: str | None


def _flight(markup: str) -> bytes:
    """The Next.js (React Server Components) data the page carries in its scripts, as one stream."""
    parts: list[str] = []
    pos = 0
    # str.find from a position that only moves forward: linear in the page size.
    while (start := markup.find(_PUSH, pos)) >= 0:
        end = markup.find(_PUSH_END, start)
        if end < 0:
            break
        pos = end + len(_PUSH_END)
        try:
            chunk = json.loads(markup[start + len(_PUSH) : end])
        except (ValueError, RecursionError):
            continue
        if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
    return "".join(parts).encode("utf-8", errors="replace")


def flight_rows(markup: str) -> dict[str, bytes]:
    """Rows of the page's Next.js data by id: ``<hex id>:<json>\\n`` or ``<hex id>:T<hex length>,<text>``.

    A text row has no terminator; its length counts UTF-8 bytes.
    """
    stream = _flight(markup)
    rows: dict[str, bytes] = {}
    pos, size = 0, len(stream)
    while pos < size and len(rows) < MAX_ROWS:
        colon = stream.find(b":", pos, pos + 9)
        if colon < 0 or not _ROW_ID_RE.fullmatch(stream, pos, colon):
            break
        row_id = stream[pos:colon].decode("ascii")
        if stream[colon + 1 : colon + 2] == b"T":
            comma = stream.find(b",", colon + 2, colon + 12)
            try:
                length = int(stream[colon + 2 : comma], 16) if comma > 0 else -1
            except ValueError:
                length = -1
            if length < 0:
                break
            rows[row_id] = stream[comma + 1 : comma + 1 + length]
            pos = comma + 1 + length
        else:
            newline = stream.find(b"\n", colon)
            end = size if newline < 0 else newline
            rows[row_id] = stream[colon + 1 : end]
            pos = end + 1
    return rows


def _news_items(rows: dict[str, bytes]) -> list[Any]:
    for row in rows.values():
        if b'"newsItems"' not in row:
            continue
        text = row.decode("utf-8", errors="replace")
        if (m := _NEWS_ITEMS_RE.search(text)) is None:
            continue
        try:
            items, _ = json.JSONDecoder().raw_decode(text, m.end())
        except (ValueError, RecursionError):
            fail("Sumy: newsItems in the news page are not JSON")
        data = items.get("data") if isinstance(items, dict) else None
        if not isinstance(data, list):
            fail("Sumy: newsItems in the news page have no data list")
        return data[:MAX_ITEMS]
    fail("Sumy: no newsItems in the news page")


def _content(item: dict[str, Any], rows: dict[str, bytes]) -> str:
    content = item.get("content")
    text = content.get("content") if isinstance(content, dict) else None
    if not isinstance(text, str):
        return ""
    if m := _REF_RE.fullmatch(text):
        text = rows.get(m[1], b"")[:MAX_CONTENT].decode("utf-8", errors="replace")
    return text[:MAX_CONTENT]


def _day(item: dict[str, Any], content: str) -> date | None:
    """The day a post is for: its ``date``, else «на 20-10-2026» in the title or the text."""
    value = item.get("date")
    if isinstance(value, str) and _ISO_DAY_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    title = item.get("title")
    for text in (title if isinstance(title, str) else "", content):
        if m := _DAY_RE.search(text[:MAX_CONTENT]):
            try:
                return date(int(m[3]), int(m[2]), int(m[1]))
            except ValueError:
                return None
    return None


def _posted(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
        return (moment if moment.tzinfo else moment.replace(tzinfo=KYIV)).astimezone(KYIV)
    except (ValueError, OverflowError):  # OverflowError: out of range in Kyiv time
        return None


def parse_news(markup: str) -> tuple[list[Post], date | None]:
    """Schedule posts on a news page, and the day of the page's last (oldest) item."""
    rows = flight_rows(markup)
    posts, last = [], None
    for item in _news_items(rows):
        if not isinstance(item, dict):
            continue
        content = _content(item, rows)
        day = _day(item, content)
        last = day or last
        categories = item.get("categories")
        if not isinstance(categories, list) or not any(
            isinstance(c, dict) and c.get("name") == CATEGORY for c in categories[:20]
        ):
            continue
        url, number, posted = item.get("url"), item.get("id"), _posted(item.get("createdAt"))
        # Schedule posts have generated slugs («445/gpv-tomorrow-19-10-2026-19-34»); articles
        # about schedules in the same category do not.
        if not (isinstance(url, str) and _SLUG_RE.fullmatch(url)) or not isinstance(number, int):
            continue
        if day is None or posted is None:
            log.info("Sumy: post %s has no day or time, skipping it", number)
            continue
        image = _IMAGE_RE.search(content)
        image_url = IMAGE_URL.format(image[1]) if image else None
        posts.append(Post(number, f"{NEWS_URL}/{url}", day, posted, image_url))
    return posts, last


# --- Reading the image -------------------------------------------------------------------

#: 780 px for up to two boxes in a row, plus 272 px for every further box (up to six).
WIDTHS = frozenset(780 + 272 * extra for extra in range(5))
HEIGHTS = range(1300, 1900)
BOX_GREY = (241, 241, 241)
BOX_YELLOW = (255, 249, 205)
FILL_TOLERANCE = 3
#: Grid lines (light grey, partly transparent) have every channel above this; the blue background does not.
LINE_LEVEL = 90
#: White text (header, labels): every channel above this.
WHITE_LEVEL = 160
TEXT_GREY = 102  # #666, the length under an interval
#: The table: 13 rows (header and 12 subqueues) between 14 lines, three columns between 3 lines.
TABLE_LINES = 14
HEADER_ROW = range(50, 76)
DASH_ROW = range(80, 101)
BOX_ROW = range(115, 141)
#: Boxes: 238×109 px, the first 10 px right of the label column, then every 272 px.
BOX_OFFSET = 10
BOX_PITCH = 272
BOX_WIDTH = range(235, 242)
BOX_HEIGHT = range(104, 115)
BOX_MARGIN = 12  # a column this far inside a box is clear of text
MAX_BOXES = 6
#: Pixels left out at a box's edges when looking for its text.
BOX_INSET = (12, 4)
LINE_HEIGHT = range(18, 31)
MAX_GLYPHS = 24

Box = tuple[int, int, int, int, tuple[int, int, int]]  # x0, y0, x1, y1 and the fill colour
Glyph = tuple[int, tuple[int, ...]]  # width and rows, bit x of a row is pixel x
Decoded = tuple[dict[str, tuple[Outage, ...]], dict[str, str]]


class Font(NamedTuple):
    name: str
    #: Vertical offsets tried when comparing (labels and the header move by a pixel with their row).
    shifts: tuple[int, ...]
    templates: tuple[tuple[str, int, tuple[int, ...]], ...]  # char, width, rows


# Templates cut from 19 real images (7–20 October 2026), one per bitmap seen: a glyph renders
# a little differently at each sub-pixel position. Each is "<width>x<height>:<base64>", the
# rows' bits left to right, top to bottom. The length line has not shown 5–9 yet: a box with
# such a length stays unknown until its glyph is added. To add one, cut it from an image with
# ``box_glyphs`` (or ``header_glyphs``, ``label_glyphs``) and ``encode_glyph``.
TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "time": {
        "0": (
            "15x23:D+A/4P/j4+eDzgP8B/gP8B/gH8A/gH8A/gP8B/gP8B7gOeDx9+P/gf4B4AA=",
            "15x23:B+A/4P/h4+eBzwPcB/gH8A/gH8A/gH8A/gH8A7gPcB7wPeDx/+H/gf4A8AA=",
            "15x23:D8A/4P/j48eD3gO8B/gP4B/AP4B/AP4B/AP4B/gP8BzgeeDz98P/A/wB4AA=",
        ),
        "1": (
            "9x23:B4fH7///vZ4PB4PB4PB4PB4PB4PB4PB4PAA=",
            "9x23:A4fH5///vY4HA4HA4HA4HA4HA4HA4HA4HAA=",
        ),
        "2": (
            "15x23:H8D/4//ng8QDwAeADwAeADwA8AHgB4AeAHgB4AeAHgB4AeAH///////AAAA=",
            "15x23:D+B/4//jg+YDwAOABwAeADwAcAHgB4AeAHgB8AfAHwB8AeAH///////AAAA=",
        ),
        "3": (
            "15x23:H+D/8//ng+QDwAOABwAeAHw/8H/A/8B/wAfAB4APAB4APgD/x+//n/4H4AA=",
        ),
        "4": (
            "17x23:ADwAPgAfAB+AH8AP4A/wD3gPPAeeB48Hh4ODw8Hj///////////ADwAHgAPAAeAAAA==",
            "17x23:ADwAPgA/AB+AH8Af4A/wD3gPPAceB48Hh4eDw8Hj///////////ADwAHgAPAAeAAAA==",
        ),
        "5": (
            "15x23:P/h/8P/j4AeADwAeADwAf4D/4f/h/+ADwAPAB4APAB4APwD37+//j/4P4AA=",
            "15x23:f/j/8f/j4AeADgAcADgAf4D/4f/j/+ADwAPAB4APAB4AOwD3z+//n/wP4AA=",
        ),
        "6": (
            "15x23:A/wP+H/x8APADwAeADgAc+H/8//38++B/gP8A/gHcA7gPeB58+P/g/4B8AA=",
            "15x23:A/gf8H/h8APADwAcAHgA8+H/8//38++B/gP8B/gP8B7gPeDx9+P/g/4B8AA=",
        ),
        "7": (
            "15x23:///////4AfABwAeADwA8AHgB4APADwAeAHgA8AHAB4APADwAeAHgA8AAAAA=",
        ),
        "8": (
            "15x23:D+B/8f/zwecBzgOcBzwePHx/8H+A/4P/jwe8B/gP8A/gP8B74+f/x/8B+AA=",
            "15x23:D+B/8f/jwe8D3gOcDzwefHh/4H+B/4f/jw+8B/AP4B/AP8B/4+f/x/4D8AA=",
        ),
        "9": (
            "15x23:D+B/4f/jw+8D3gP8B/gP8B/gP+D7//P/4//AB4AOADwAeAHhH8P/B/gPwAA=",
        ),
        ":": (
            "4x23:AAAG//8AAAAA///g",
        ),
        "-": (
            "8x23:AAAAAAAAAAAAAAAA/////wAAAAAAAAA=",
        ),
    },
    "length": {
        "0": (
            "13x24:H4H+HDjAzgNgGwDYBsA2AfANgGwDYBuAzAxw4f4D4AAAAAAAAAAA",
            "13x24:D4H/DhjgZgNwH4D8A8AeAPAHwD4DsB2AzgY4YP8D4AAAAAAAAAAA",
        ),
        "1": (
            "7x24:Dj3/PGDBgwYMGDBgwYMGDBgAAAAA",
        ),
        "2": (
            "12x24:H4f84OAGAHAHAGAGAMAcA4BwDgHAOAcA4A////AAAAAAAAAA",
        ),
        "3": (
            "13x24:H4P/OBwAYAMAGAHAHB+A/gAcAGADgBwA4AbAd/8f4AAAAAAAAAAA",
            "13x24:H8P/HBwAcAOAHADAHB/AfwAcAHABgAwAYAfAd/8P4AAAAAAAAAAA",
        ),
        "4": (
            "14x24:AGADgB4AeANgGYDmAxgYYOGDBhgYwGP////AGABgAYAGAAAAAAAAAAAA",
        ),
        "г": (
            "9x24:AAAAAAAH//+AwGAwGAwGAwGAwGAwAAAAAAAA",
        ),
        "о": (
            "13x24:AAAAAAAAAAAH4P+ODmA/APAHgDwB4A+AbAdwcf8D8AAAAAAAAAAA",
        ),
        "д": (
            "15x24:AAAAAAAAAAAAAP8B/gMcBjgMcBjgYcDDgYcGDgwcMDn/////AH4A/AH4A/AH",
        ),
        "х": (
            "12x24:AAAAAAAAAA4HYGMOOcHYDwDwDwD4GYOMcOYHwDAAAAAAAAAA",
        ),
        "в": (
            "12x24:AAAAAAAAAA/4/+wGwGwGwO/4/8wGwDwDwH/+/4AAAAAAAAAA",
        ),
        ".": (
            "4x24:AAAAAAAAAAJ/YAAA",
        ),
    },
    "header": {
        "0": (
            "25x39:AP8AAf/gAf/4Af/+AfgfgfgD4PgA8PgAfHwAHjwADx4AB98AA++AAPfAAHvgAD3gAB7wAA/4AAf8AAP+AA"
            "H/AAD/gAB/wAA/4AAe+AAPfAAHvgAD3wAD54AB88AA+fAAeHgAfD4APg+APgfgfgH//wB//wAf/gAD/AA=",
            "25x39:AP8AAf/gAf/8Af//AfgPgPgD4PgA+HgAfHwAHj4AD54AB88AAe+AAPfAAHvgAD/wAB/4AA/4AAf8AAP+AA"
            "H/AAD/gAB/4AA/8AAf+AAP/AAHvgADzwAB54AB8+AA+fAAeHgAfD4APg+APgfgPwH//wB//wAf/wAD/AA=",
            "24x39:AP8AA//AD//gH//wH4H4PgD4PAB8fAA8eAA+eAAe+AAe+AAf8AAf8AAf8AAP8AAP8AAP8AAP8AAP8AAP8A"
            "AP8AAP8AAP8AAP8AAP8AAf8AAf+AAf+AAeeAAeeAA+fAA+PAB8PgD8H4H4D//wB//gA//AAP8A",
        ),
        "1": (
            "14x39:AAAB8A/Afwf8P/Pz34/8feHyB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8"
            "AfAHwB8AAA",
            "14x39:AAAB8A/A/wf8P/P335/8fcHyB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8AfAHwB8"
            "AfAHwB8AAA",
            "13x39:AAADwH4H8H+P/P3vz/h7g9geAPAHgDwB4A8AeAPAHgDwB4A8AeAPAHgDwB4A8AeAPAHgDwB4A8AeAPAHgD"
            "wAAA==",
        ),
        "2": (
            "24x39:Af8AD//AH//wf//4/gH4eAD8MAB8AAA+AAA+AAA+AAA+AAA+AAA+AAA8AAB8AAB8AAD4AAD4AAHwAAPgAA"
            "fgAA/AAB+AAD8AAH4AAPwAAfgAA/AAB+AAD8AAH4AAPwAAfgAA/AAA////////////////AAAA",
            "25x39:AP+AA//wB//8B///B/APweAD4MAA+AAAfAAAHgAADwAAB4AAA8AAA+AAAfAAAPgAAPgAAHwAAHwAAHwAAD"
            "4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD4AAD////////////////wAAAA=",
            "25x39:AP+AA//wB//8B///B/APweAD8EAA+AAAfAAAHgAADwAAB4AAA8AAAeAAAfAAAPgAAHgAAHwAAHwAAD4AAD"
            "4AAD4AAD4AAD4AAD8AAD8AAD8AAD8AAD8AAD8AAD4AAD4AAD4AAD4AAD4AAD////////////////wAAAA=",
        ),
        "3": (
            "25x39:Af+AB//wD//+D///h+APwcAD8AAA+AAAPgAAHwAAD4AAB8AAA+AAA+AAAfAAAfAAAfgAB/gB//AA/+AAf/"
            "4AP//AAAfwAAD4AAA+AAAfAAAHwAAD4AAB8AAA+AAAfAAAPgAAPgAAH2AAHz4AP5///4///4P//wA//AA=",
        ),
        "4": (
            "28x39:AAAAAAAHwAAA/AAAD8AAAfwAAD/AAAP8AAB7wAAPvAAA88AAHjwAA+PAAHw8AAeDwAD4PAAfA8AB4DwAPA"
            "PAB8A8AHgDwA8APAHwA8A+ADwDwAPAfAA8D4ADwP//////////////////AAA8AAADwAAAPAAAA8AAADwAAAPAAA"
            "A8AAADwAAAAAA=",
        ),
        "5": (
            "24x39:AAAAH//4P//4P//4P//4PAAAPAAAPAAAPAAAPAAAPAAAPAAAPAAAPAAAfAAAfAAAf/8Af//gf//wP//4AA"
            "H8AAB+AAA+AAA+AAAfAAAfAAAfAAAfAAAfAAAfAAAeAAA+AAA+wAB88AH8///4///gf//AD/4A",
        ),
        "6": (
            "24x39:AA/4AH/4Af/4A//4B/AAD8AAH4AAHwAAPgAAPgAAfAAAfAAAeAAAeAAA+AAA+H+A+f/w+//4///8/wB+/g"
            "A+/AAf+AAf+AAP+AAP8AAP8AAP+AAP+AAPeAAPfAAffAAfPgA+PwB+H4D8D//4B//wA//gAH+A",
        ),
        "7": (
            "25x39:AAAAf///////////////+AAAfAAAPAAAPgAAHgAAHwAAD4AAD4AAB8AAB8AAA+AAAeAAAfAAAPAAAPgAAH"
            "gAAHwAAD4AAD4AAB8AAB8AAA+AAA+AAAfAAAfAAAPgAAHgAAHwAAD4AAD4AAB8AAB8AAA+AAA+AAAAAAA=",
        ),
        "8": (
            "24x39:Af8AB//gD//wH//4PwD8fgB8fAA+eAA+eAAeeAAeeAA+fAA+fAA8PgB8PwD4H4HwD+fgB//AAf8AA//AD/"
            "/gH4f4PwH8fAB+fAA++AAf8AAf8AAP8AAP8AAP8AAP8AAf+AAf/AA+fgD+P//8H//4D//gAf+A",
        ),
        "9": (
            "24x39:Af4AB//AD//gH//wPwH4fgD4fAB8+AA8+AA+8AAe8AAe8AAf8AAP8AAP8AAf8AAf+AAf+AA/fAB/fgD/P/"
            "//H//fD/+fA/wfAAAeAAAeAAAeAAA+AAA8AAB8AAB8AAD4AAH4AAPwAA/gP//AP/+AP/4AP/AA",
        ),
        ".": (
            "6x39:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAe/////eA",
        ),
        "Г": (
            "21x39:AAAH//////////////gAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHg"
            "AA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAA8AAHgAAAAAAA==",
        ),
        "П": (
            "28x39:AAAAD///////////////////AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AA"
            "Af8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8AAB/wAAH/AAAf8A"
            "AB/wAAHwAAAAA=",
        ),
        "В": (
            "24x39:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+A"
            "AP+AAP////////////////+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAP+AAPAAAA",
        ),
        "н": (
            "25x39:AAAAf//AP//8H///D///x4AH88AA/eAAPvAAH3gAD7wAA94AA+8AAfeAAPvAAPngAPzwAfx///g///Af//"
            "4P///HgAPzwAB94AA/8AAP+AAH/AAB/gAA/wAA/4AAf8AAP+AAP/AAP3gAf7///5///4///wf//gAAAAA=",
        ),
        "а": (
            "23x39:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAD/gA//wH//4H//wPAPwAAPgAAPAAAfAAA+AAB8AAD4A"
            "//wP//h///P/A+fgB98AD74AH/gAP/AAf+AA/8AD/4AP/4Af34D/v//PP/8eP/w8H+AAA=",
        ),
    },
    "label": {
        "1": (
            "15x37:AH4B/A/4P/D/4//f////fn74/OH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH4A/AH"
            "4A/AH4A/AAAA==",
        ),
        "2": (
            "25x38:ADwAAf/gA//8B///B///w///4PwH+HgA/BgAfgAAPwAAH4AAD8AAB+AAA/AAA/gAAfgAAfwAAfwAAP4AAP"
            "4AAP4AAP4AAP8AAP8AAP8AAP8AAP8AAP8AAP4AAP4AAP4AAP////////////////////////gAAAA=",
        ),
        "3": (
            "25x38:ADwAAf/gB//8B///D///w///8fgD+HAA/AAAfwAAP4AAH8AAD+AAB+AAB/AAB/AAH/gH//gD//gB//wA//"
            "+Af//gAA/wAAH8AAB+AAA/gAAfwAAP4AAH8AAD+AAB+4AB/fgD/v///n///j///g///AH/+AAAAAA=",
        ),
        "4": (
            "28x37:AAD8AAAfwAAB/AAAP8AAB/wAAH/AAA/8AAH/wAA//AAD/8AAfnwAD+fAAPx8AB+HwAP4fAA/B8AH4HwA/A"
            "fAH8B8AfgHwD8AfAfwB8B+AP4P//////////////////////8AAHwAAAfAAAB8AAAHwAAAfAAAB8AAAHwAAAfAAA"
            "AAAA==",
        ),
        "5": (
            "24x38:AAAAP//4P//4P//4P//4P//4PwAAPgAAPgAAPgAAfgAAfgAAfgAAfgAAf/4Af//Af//wf//4f//8H7/8AA"
            "H+AAD+AAB/AAB/AAA/AAA/AAA/AAB/AAB+AAD+4AD++AP8///8///4///gf//AH/8AAAAA",
        ),
        "6": (
            "24x38:AAPgAH/8Af/8A//8B//8D/98H+AAH8AAP4AAPwAAfwAAfgAAfgAA/AAA/P/A///w///4///8///+/4D+/w"
            "B//gB//AA//AA//AA//AA//AA//AA/fgA/fgB/fwD+P8H+H//8H//4D//4A//gAf+AAAAA",
        ),
        ".": (
            "8x38:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAYfv7+//7+fBg=",
        ),
    },
}  # fmt: skip


def _decode_template(code: str) -> tuple[int, tuple[int, ...]]:
    size, _, data = code.partition(":")
    width, height = (int(n) for n in size.split("x"))
    flat = "".join(format(byte, "08b") for byte in base64.b64decode(data))
    return width, tuple(int(flat[r * width : (r + 1) * width][::-1], 2) for r in range(height))


@cache
def font(name: str) -> Font:
    shifts = (-1, 0, 1) if name in ("header", "label") else (0,)
    templates = tuple(
        (char, *_decode_template(code)) for char, codes in TEMPLATES.get(name, {}).items() for code in codes
    )
    return Font(name, shifts, templates)


def encode_glyph(glyph: Glyph) -> str:
    """A glyph as a template string (used to cut templates from sample images)."""
    width, rows = glyph
    flat = "".join(format(row, f"0{width}b")[::-1] for row in rows)
    flat += "0" * (-len(flat) % 8)
    data = bytes(int(flat[i : i + 8], 2) for i in range(0, len(flat), 8))
    return f"{width}x{len(rows)}:{base64.b64encode(data).decode()}"


def _distance(glyph: Glyph, template: tuple[int, tuple[int, ...]], shifts: Sequence[int]) -> int:
    (_, rows), (_, reference) = glyph, template
    best = 1 << 30
    for dy in shifts:
        for dx in (-1, 0, 1):
            total = 0
            for y in range(min(0, dy), max(len(rows), len(reference) - dy)):
                a = rows[y] if 0 <= y < len(rows) else 0
                b = reference[y + dy] if 0 <= y + dy < len(reference) else 0
                total += ((a << max(dx, 0)) ^ (b << max(-dx, 0))).bit_count()
            best = min(best, total)
    return best


def match(glyph: Glyph, font: Font) -> str | None:
    """The character a glyph is, or ``None`` unless one template is clearly the closest."""
    width, rows = glyph
    ink = sum(row.bit_count() for row in rows)
    best: dict[str, int] = {}
    for char, t_width, t_rows in font.templates:
        if abs(t_width - width) <= 2 and abs(len(t_rows) - len(rows)) <= max(font.shifts):
            d = _distance(glyph, (t_width, t_rows), font.shifts)
            best[char] = min(best.get(char, d), d)
    ranked = sorted(best.items(), key=lambda item: item[1])
    # In the samples, renderings of one character differ by at most a third of its ink, and
    # different characters by much more: accept only a close match with a clear runner-up.
    if not ranked or ranked[0][1] > max(3, ink // 3):
        return None
    if len(ranked) > 1 and ranked[1][1] < 2 * ranked[0][1] + 8:
        return None
    return ranked[0][0]


def _runs(flags: Iterable[bool]) -> list[tuple[int, int]]:
    runs, start, i = [], None, 0
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, i + 1))
    return runs


def _byte_runs(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    """[a, b) runs of non-zero bytes in ``data[start:end]``, linear and in C where it can be."""
    runs, pos = [], start
    while pos < end and (a := data.find(b"\xff", pos, end)) >= 0:
        b = data.find(b"\x00", a, end)
        pos = end if b < 0 else b
        runs.append((a - start, pos - start))
    return runs


class _Planes(NamedTuple):
    """Per-pixel masks of one image, 255 where true, as bytes (row-major) for fast counting."""

    width: int
    height: int
    light: bytes  # grid lines
    white: bytes  # header and labels
    fills: dict[tuple[int, int, int], bytes]  # box colours
    luma: Image.Image
    rgb: Image.Image


def _planes(image: Image.Image) -> _Planes:
    r, g, b = image.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    fills = {}
    for color in (BOX_GREY, BOX_YELLOW):
        dr, dg, db = ImageChops.difference(image, Image.new("RGB", image.size, color)).split()
        farthest = ImageChops.lighter(ImageChops.lighter(dr, dg), db)
        fills[color] = farthest.point([255 if v <= FILL_TOLERANCE else 0 for v in range(256)]).tobytes()
    return _Planes(
        *image.size,
        darkest.point([255 if v >= LINE_LEVEL else 0 for v in range(256)]).tobytes(),
        darkest.point([255 if v >= WHITE_LEVEL else 0 for v in range(256)]).tobytes(),
        fills,
        image.convert("L"),
        image,
    )


def _not_at_edges(runs: list[tuple[int, int]], size: int) -> list[tuple[int, int]]:
    return [(a, b) for a, b in runs if a > 0 and b < size]


class Table(NamedTuple):
    top: int
    #: x of the left line, of the right edge of the line after the labels, and of the right line.
    columns: list[int]
    #: [top, bottom) of the 12 subqueue rows.
    rows: list[tuple[int, int]]


def locate_table(p: _Planes) -> Table:
    """The table's lines: 14 across (top, under the header, after each subqueue) and 3 down."""
    w, h = p.width, p.height
    x0, x1 = 45, w - 45
    lines = _not_at_edges(
        _runs(p.light.count(255, y * w + x0, y * w + x1) >= 0.9 * (x1 - x0) for y in range(h)), h
    )
    if len(lines) != TABLE_LINES:
        fail(f"Sumy: expected {TABLE_LINES} table lines, found {len(lines)}")
    rows = [(a[1], b[0]) for a, b in zip(lines, lines[1:], strict=False)]
    top, bottom = lines[0][1] + 2, lines[-1][0] - 2
    columns = Image.frombytes("L", (w, h), p.light).transpose(Image.Transpose.TRANSPOSE).tobytes()
    verticals = _not_at_edges(
        _runs(columns.count(255, x * h + top, x * h + bottom) >= 0.9 * (bottom - top) for x in range(w)), w
    )
    if len(verticals) != 3:
        fail(f"Sumy: expected 3 table columns lines, found {len(verticals)}")
    (left, _), (sep, sep_end), (right, _) = verticals
    if not (left < 60 and 150 < sep < 230 and right > w - 60):
        fail("Sumy: the table columns are not where they should be")
    if rows[0][1] - rows[0][0] not in HEADER_ROW:
        fail("Sumy: the table header has an unexpected height")
    return Table(lines[0][0], [left, sep_end, right], rows[1:])


def _ink_glyphs(ink: list[list[bool]]) -> list[Glyph] | None:
    """Glyphs of one line of text (rows of ink flags), split at blank columns, cut to the line's height.

    The line is the tallest run of rows with ink: a speck of the background above or below it
    does not change its height.
    """
    lines = _runs(any(row) for row in ink)
    if not lines:
        return None
    top, bottom = max(lines, key=lambda run: run[1] - run[0])
    rows = ink[top:bottom]
    width = len(rows[0])
    glyphs = []
    for a, b in _runs(any(row[x] for row in rows) for x in range(width)):
        glyphs.append((b - a, tuple(sum(1 << (x - a) for x in range(a, b) if row[x]) for row in rows)))
        if len(glyphs) > MAX_GLYPHS:
            return None
    return glyphs


def _white_glyphs(p: _Planes, x0: int, x1: int, y0: int, y1: int) -> list[Glyph] | None:
    ink = [[p.white[y * p.width + x] == 255 for x in range(x0, x1)] for y in range(y0, y1)]
    return _ink_glyphs(ink)


#: Where the header text is: right of the logo, above the table.
HEADER_BOX = (140, 40, 660)


def header_glyphs(p: _Planes, table_top: int) -> list[Glyph] | None:
    x0, y0, x1 = HEADER_BOX
    return _white_glyphs(p, x0, x1, y0, table_top - 8)


def label_glyphs(p: _Planes, columns: list[int], row: tuple[int, int]) -> list[Glyph] | None:
    return _white_glyphs(p, columns[0] + 4, columns[1] - 4, row[0] + 4, min(row[1], row[0] + 80) - 4)


def read(glyphs: Iterable[Glyph] | None, name: str) -> str | None:
    """The text of a line, ``?`` for each glyph no template matches; ``None`` if there are no glyphs."""
    if glyphs is None:
        return None
    f = font(name)
    return "".join(match(g, f) or "?" for g in glyphs)


_HEADER_DATE_RE = re.compile(r"(\d\d)\.(\d\d)\.(\d{4})")


def header_day(p: _Planes, table_top: int) -> date:
    text = read(header_glyphs(p, table_top), "header")
    m = _HEADER_DATE_RE.search(text or "")
    if m is None:
        fail(f"Sumy: the image header has no readable date ({text!r})")
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        fail(f"Sumy: the image header has an impossible date ({m[0]})")


def find_boxes(p: _Planes, columns: list[int], row: tuple[int, int]) -> list[Box] | None:
    """Outage boxes in a row, left to right; ``None`` if anything box-coloured is not a box we know."""
    w = p.width
    x_start, x_end = columns[1] + 1, columns[2] - 1
    lefts: dict[int, tuple[int, int, int]] = {}
    for y in range(row[0] + 2, row[1] - 2, 3):
        for color, mask in p.fills.items():
            for a, b in _byte_runs(mask, y * w + x_start, y * w + x_end):
                if b - a >= 120:
                    lefts.setdefault(x_start + a, color)
                    if b - a < min(BOX_WIDTH):
                        lefts[x_start + a] = (-1, -1, -1)  # a long run of box colour that is not a box
        if len(lefts) > 20 * MAX_BOXES:  # rounded corners give a box a dozen left edges
            return None
    # A box's left edge, then rows down a column just inside it.
    boxes: list[Box] = []
    for x, color in sorted(lefts.items()):
        if any(box[0] <= x < box[2] for box in boxes):
            continue
        if color not in p.fills:
            return None
        mask = p.fills[color]
        column = [mask[y * w + x + BOX_MARGIN] == 255 for y in range(row[0], row[1])]
        spans = [(row[0] + a, row[0] + b) for a, b in _runs(column) if b - a > 20]
        if len(spans) != 1:
            return None
        (y0, y1), right = spans[0], x + 1
        while right < x_end and mask[(y0 + y1) // 2 * w + right] == 255:
            right += 1
        boxes.append((x, y0, right, y1, color))
    if len(boxes) > MAX_BOXES:
        return None
    for i, (x0, y0, x1, y1, _) in enumerate(boxes):
        if (
            x0 - columns[1] - BOX_OFFSET not in range(i * BOX_PITCH - 3, i * BOX_PITCH + 4)
            or x1 - x0 not in BOX_WIDTH
            or y1 - y0 not in BOX_HEIGHT
        ):
            return None
    return boxes


def _unknown_box_after(image: Image.Image, columns: list[int], row: tuple[int, int], count: int) -> bool:
    """Whether the slot after the last box holds an even rectangle (a box of a colour we do not know).

    The background is a blue picture with gradients and specks: never even across a box.
    """
    x = columns[1] + BOX_OFFSET + count * BOX_PITCH
    if x + max(BOX_WIDTH) >= columns[2]:
        return False
    y = row[0] + (row[1] - row[0] - min(BOX_HEIGHT)) // 2
    colors = [image.getpixel((x + dx, y + dy)) for dx in (BOX_MARGIN, 120, 226) for dy in (6, 58, 100)]
    even = all(max(channel) - min(channel) <= 8 for channel in zip(*colors, strict=True))
    r, g, b = colors[0]
    return even and (r >= 80 or r * 299 + g * 587 + b * 114 >= 60_000)


def _has_dash(p: _Planes, columns: list[int], row: tuple[int, int]) -> bool:
    """The «—» of a row without outages: a short light bar at the start of the cell."""
    x0, x1 = columns[1] + 4, columns[1] + 60
    luma = p.luma
    for y in range(row[0] + 15, row[0] + 60):
        bar = [luma.getpixel((x, y)) - luma.getpixel((x, y - 3)) >= 30 for x in range(x0, x1)]
        runs = [b - a for a, b in _runs(bar)]
        if runs and 20 <= max(runs) <= 40:
            return True
    return False


def box_glyphs(p: _Planes, box: Box) -> list[list[Glyph] | None] | None:
    """Glyphs of a box's two lines: the interval and its length."""
    x0, y0, x1, y1, color = box
    fill = Image.new("RGB", (1, 1), color).convert("L").getpixel((0, 0))
    dx, dy = BOX_INSET
    crop = p.luma.crop((x0 + dx, y0 + dy, x1 - dx, y1 - dy))
    width, height = crop.size
    pixels = crop.tobytes()
    rows = [pixels[y * width : (y + 1) * width] for y in range(height)]
    lines = _runs(min(row) <= fill - 40 for row in rows)
    if len(lines) != 2 or any(b - a not in LINE_HEIGHT for a, b in lines):
        return None
    out = []
    for (a, b), level in zip(lines, (fill // 2, (fill + TEXT_GREY) // 2), strict=True):
        ink = [[v <= level for v in rows[y]] for y in range(max(0, a - 1), min(height, b + 1))]
        out.append(_ink_glyphs(ink))
    return out


_TIME_RE = re.compile(r"(\d\d):(\d\d)-(\d\d):(\d\d)")
_LENGTH_RE = re.compile(r"(\d{1,2})год\.(\d\d)хв\.")


def read_box(p: _Planes, box: Box) -> tuple[int, int] | None:
    """Minutes since midnight at the start and the end of a box's interval, if its text checks out."""
    lines = box_glyphs(p, box)
    if lines is None:
        return None
    interval, length = read(lines[0], "time"), read(lines[1], "length")
    m, n = _TIME_RE.fullmatch(interval or ""), _LENGTH_RE.fullmatch(length or "")
    if m is None or n is None:
        return None
    h1, m1, h2, m2, hours, minutes = (int(v) for v in (*m.groups(), *n.groups()))
    if max(h1, h2) > 24 or max(m1, m2, minutes) > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if end == 0:
        end = 24 * 60  # «22:00 - 00:00» ends at midnight
    if not start < end <= 24 * 60 or end - start != hours * 60 + minutes:
        return None
    return start, end


def _read_group(
    p: _Planes, columns: list[int], row: tuple[int, int], group: str, day: date
) -> tuple[Outage, ...] | None:
    """A group's outages, or ``None`` if any check fails."""
    height = row[1] - row[0]
    if height not in DASH_ROW and height not in BOX_ROW:
        return None
    if read(label_glyphs(p, columns, row), "label") != group:
        return None
    boxes = find_boxes(p, columns, row)
    if boxes is None:
        return None
    if not boxes:
        return () if height in DASH_ROW and _has_dash(p, columns, row) else None
    if height not in BOX_ROW or _unknown_box_after(p.rgb, columns, row, len(boxes)):
        return None
    outages, previous_end = [], 0
    for box in boxes:
        interval = read_box(p, box)
        if interval is None or interval[0] < previous_end:
            return None
        previous_end = interval[1]
        outages.append(Outage(at_minute(day, interval[0]), at_minute(day, interval[1])))
    return merge_outages(outages)


def _png_width(png: bytes) -> int | None:
    if png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR":
        return int.from_bytes(png[16:20], "big")
    return None


def parse_image(png: bytes, day: date) -> Decoded:
    """Outages by group from one schedule PNG for ``day``, and the groups that could not be read."""
    width = _png_width(png)
    if width is not None and width not in WIDTHS:
        # Checked before decoding anything: only these widths are the table.
        fail(f"Sumy: image is {width} px wide, expected one of {sorted(WIDTHS)}")
    image = decode_png(png, (width or min(WIDTHS), HEIGHTS), "Sumy")
    planes = _planes(image)
    table = locate_table(planes)
    if (shown := header_day(planes, table.top)) != day:
        fail(f"Sumy: the image is for {shown:%d.%m.%Y}, the post for {day:%d.%m.%Y}")
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    for group, row in zip(GROUPS, table.rows, strict=True):
        outages = _read_group(planes, table.columns, row, group, day)
        if outages is None:
            unknown[group] = UNRECOGNISED
        else:
            groups[group] = outages
    return groups, unknown


# --- The source --------------------------------------------------------------------------


class SumyNews(Provider):
    hosts = frozenset({"www.soe.com.ua"})
    min_interval = 300
    kind = Kind.PICTURE
    page = NEWS_URL

    def __init__(self) -> None:
        # Decoded images (or why they could not be read) by URL and day. Every upload gets a
        # new name, so a known URL is an unchanged image: no need to download it every poll.
        self._decoded: dict[tuple[str, date], Decoded | str] = {}

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        days = (today, today + timedelta(days=1))
        posts, last = await asyncio.to_thread(parse_news, (await http.get(NEWS_URL)).text())
        if today not in {post.day for post in posts} and last is not None and last >= today:
            # Today's posts are pushed to the next page by tomorrow's and other news.
            more, _ = await asyncio.to_thread(parse_news, (await http.get(NEWS_URL, params={"page": "2"})).text())
            posts += more

        newest: dict[date, Post] = {}
        for post in posts:
            if post.day in days and (post.day not in newest or newest[post.day].posted < post.posted):
                newest[post.day] = post

        schedules, decoded, errors = {}, {}, []
        for day, post in sorted(newest.items()):
            if post.image_url is None:
                # A newer post we cannot read (the table not attached yet, or a text-only
                # change): the older table may be out of date, so every group is unknown.
                log.warning("Sumy: post %s for %s has no schedule image", post.id, day)
                unknown = dict.fromkeys(GROUPS, UNRECOGNISED)
                schedules[day] = DaySchedule(day, post.posted, {}, unknown, (NO_IMAGE.format(post.url),))
                continue
            key = (post.image_url, day)
            if (result := self._decoded.get(key)) is None:
                png = (await http.get(post.image_url)).body
                try:
                    # Decoding takes a fraction of a second of CPU: keep it off the event loop.
                    result = await asyncio.to_thread(parse_image, png, day)
                except SourceFormatError as err:
                    # The message only: the exception's traceback holds the decoder's frames
                    # (the PNG, the image, its planes), and raising it again would grow it.
                    result = str(err)
            decoded[key] = result
            if isinstance(result, str):
                errors.append(result)
                continue
            groups, unknown = result
            schedules[day] = DaySchedule(day, post.posted, groups, unknown, image_url=post.image_url)
        self._decoded = decoded
        if errors:
            fail(errors[0])
        return schedules
