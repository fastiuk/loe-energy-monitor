"""Khmelnytskoblenergo: a PNG grid in a news post per schedule day on ``hoe.com.ua``.

- The news list ``/post/novini-kompaniji`` links one post per schedule day, «Погодинні
  відключення на 17 жовтня (ОНОВЛЕНО)», dated «17 Жов 2026» (the year comes from there).
  Changes during the day are added to the same post, newest on top; no post means no
  schedules are applied.
- Each version is an image ``/Content/Uploads/YYYY/MM/fileYYYYMMDDHHMMSSmmm.png``: the name is
  its upload time in Kyiv, so the newest name is the schedule in force.
- The image is 1920 px wide, its height varies with the template: the grid is found from the
  ink of its lines. 12 rows (1.1–6.2), 24 hourly columns, each read as two half-hours.
  #8FAADC — no power, white — power, #CFCDCD — maybe (in the legend; not seen in a grid yet).
- A text correction published after the newest image («підчерга 2.1 - відключення триватимуть
  з 16:00 до 17:00») is shown as a note: the image does not include it yet.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from PIL import Image

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._html import Node, parse_html
from ._image import decode_png
from ._text import MONTHS
from .base import Provider, fail

BASE_URL = "https://hoe.com.ua"
NEWS_URL = BASE_URL + "/post/novini-kompaniji"
GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
WIDTH = 1920
HEIGHTS = range(800, 1400)
OFF, ON, MAYBE = (143, 170, 220), (255, 255, 255), (207, 205, 205)
COLOR_TOLERANCE = 8
#: Luminance below this is ink: lines, text and coloured cells.
INK_LEVEL = 240
INSET = 5
UNRECOGNISED = "Нерозпізнаний графік"

_TITLE_RE = re.compile(
    r"Погодинні\s{1,3}відключення\s{1,3}на\s{1,3}(\d{1,2})\s{1,3}(" + "|".join(MONTHS) + r")", re.IGNORECASE
)
_SHORT_MONTHS = ("січ", "лют", "бер", "кві", "тра", "чер", "лип", "сер", "вер", "жов", "лис", "гру")
_POSTED_RE = re.compile(r"(\d{1,2})\s{1,3}(" + "|".join(_SHORT_MONTHS) + r")\w{0,8}\.?\s{1,3}(\d{4})", re.IGNORECASE)
_POST_PATH_RE = re.compile(r"/post/[A-Za-z0-9_-]{1,160}\.html")
_IMAGE_RE = re.compile(r"/Content/Uploads/\d{4}/\d{2}/file(\d{17})\.png", re.IGNORECASE)
_CORRECTION_RE = re.compile(r"підчерг", re.IGNORECASE)

Span = tuple[int, int]
Decoded = tuple[dict[str, tuple[Outage, ...]], dict[str, str]]


def parse_news(markup: str) -> list[tuple[str, date]]:
    """(post path, schedule day) for every schedule post in the news list, newest first."""
    root = parse_html(markup)
    articles = root.find_all("article", cls="item")
    if not articles:
        fail("Khmelnytskyi: no news items on the page")
    posts = []
    for article in articles:
        heading = article.find(cls="heading")
        link = heading.find("a") if heading else None
        if link is None or not _POST_PATH_RE.fullmatch(link.attrs.get("href", "")):
            continue
        title, posted = _TITLE_RE.search(link.text()[:300]), _POSTED_RE.search(article.text()[:2000])
        if title is None or posted is None:
            continue
        day, month = int(title[1]), MONTHS[title[2].lower()]
        posted_month = _SHORT_MONTHS.index(posted[2].lower()) + 1
        year = int(posted[3]) + (1 if posted_month == 12 and month == 1 else 0)
        try:
            posts.append((link.attrs["href"], date(year, month, day)))
        except ValueError:
            continue
    return posts


def parse_post(markup: str) -> tuple[str | None, datetime | None, str | None]:
    """The newest schedule image of a post, its upload time, and a text correction made after it."""
    root = parse_html(markup)
    body = root.find("div", cls="post")
    if body is None:
        fail("Khmelnytskyi: the post has no body")
    images = []
    for img in body.find_all("img"):
        if m := _IMAGE_RE.fullmatch(img.attrs.get("src", "")):
            images.append((m[1], m[0]))
    if not images:
        return None, None, None
    stamp, path = max(images)
    try:
        uploaded = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S").replace(tzinfo=KYIV)
    except ValueError:
        return None, None, None
    return path, uploaded, _correction_before(body, path)


def _correction_before(body: Node, path: str) -> str | None:
    """Text about a subqueue that comes before (= after, in time) the image at ``path``."""
    text: list[str] = []
    stack: list[Node | str] = [body]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            text.append(item)
        elif item.tag == "img" and item.attrs.get("src", "") == path:
            break
        else:
            if item.tag in ("br", "p", "hr"):
                text.append("\n")
            stack.extend(reversed(item.children))
    lines = [" ".join(line.split()) for line in "".join(text).split("\n")]
    corrections = [line for line in lines if _CORRECTION_RE.search(line)]
    if not corrections:
        return None
    return ("Після останнього графіка оператор змінив: " + "; ".join(corrections))[:300]


def _runs(flags: Sequence[bool]) -> list[Span]:
    runs, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(flags)))
    return runs


def locate_grid(image: Image.Image) -> tuple[list[Span], list[Span]]:
    """The 24 hour columns and the 12 group rows, found from the ink of the table's lines."""
    width, height = image.size
    ink = image.convert("L").point(lambda v: 1 if v < INK_LEVEL else 0).tobytes()

    # The table's frame: rows inked across most of the width (top, under the header, bottom).
    frame = _runs([sum(ink[y * width : (y + 1) * width]) >= 0.8 * width for y in range(height)])
    if len(frame) < 3:
        fail("Khmelnytskyi: no table frame in the image")
    top, header_bottom, bottom = frame[0], frame[1], frame[-1]
    y0, y1 = top[1] + 3, header_bottom[0] - 3
    if y1 - y0 < 20 or bottom[0] - header_bottom[1] < 200:
        fail("Khmelnytskyi: the table frame has an unexpected shape")

    # Column lines: inked through the whole header (between them the header text has gaps).
    lines = _runs([sum(ink[y * width + x] for y in range(y0, y1)) >= 0.95 * (y1 - y0) for x in range(width)])
    if len(lines) not in (28, 29):  # "Черга/підчерга" is one merged cell or two
        fail(f"Khmelnytskyi: expected 28 or 29 column lines, found {len(lines)}")
    spans = [(a[1], b[0]) for a, b in zip(lines, lines[1:], strict=False)]
    hours = spans[-26:-2]  # then "тривалість" and "частка"
    widths = [end - start for start, end in hours]
    if max(widths) - min(widths) > 3 or min(widths) < 30:
        fail("Khmelnytskyi: uneven hour columns")

    # The subqueue label cell, just left of the hours: its left line runs through the whole
    # body (in some templates the header merges it with the queue cell, the body does not).
    b0, b1 = header_bottom[1] + 2, bottom[0] - 2
    xs = range(lines[0][0], hours[0][0])
    full = _runs([sum(ink[y * width + x] for y in range(b0, b1)) >= 0.95 * (b1 - b0) for x in xs])
    if len(full) < 2:
        fail("Khmelnytskyi: no label column in the table")
    x = xs.start + full[-2][1] + 3

    # Row lines: a strip at the left edge of that cell is inked only on them.
    body = range(header_bottom[0] - 2, bottom[1] + 2)
    edges = _runs([y in body and all(ink[y * width + x + dx] for dx in range(4)) for y in range(height)])
    rows = [(a[1], b[0]) for a, b in zip(edges, edges[1:], strict=False)]
    if len(rows) != len(GROUPS) or min(end - start for start, end in rows) < 2 * INSET + 10:
        fail(f"Khmelnytskyi: expected {len(GROUPS)} group rows, found {len(rows)}")
    return hours, rows


def _near(color: Sequence[int], reference: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= COLOR_TOLERANCE for a, b in zip(color, reference, strict=True))


def _slot(image: Image.Image, box: tuple[int, int, int, int]) -> Slot | None:
    colors = image.crop(box).getcolors(64)
    if not colors:
        return None
    count, color = max(colors)
    if count < 0.97 * (box[2] - box[0]) * (box[3] - box[1]):
        return None
    for reference, slot in ((OFF, Slot.OFF), (ON, Slot.ON), (MAYBE, Slot.MAYBE)):
        if _near(color, reference):
            return slot
    return None


def parse_image(png: bytes, day: date) -> Decoded:
    """Outages by group from one schedule PNG, and the groups whose cells were unrecognised."""
    image = decode_png(png, (WIDTH, HEIGHTS), "Khmelnytskyi")
    hours, rows = locate_grid(image)
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    for group, (top, bottom) in zip(GROUPS, rows, strict=True):
        slots = []
        for left, right in hours:
            middle = (left + right) // 2
            for a, b in ((left + INSET, middle - 3), (middle + 3, right - INSET)):
                slots.append(_slot(image, (a, top + INSET, b, bottom - INSET)))
        if None in slots:
            unknown[group] = UNRECOGNISED
        else:
            groups[group] = outages_from_slots(day, slots)  # type: ignore[arg-type]
    return groups, unknown


class Khmelnytskyi(Provider):
    hosts = frozenset({"hoe.com.ua"})
    min_interval = 300

    def __init__(self) -> None:
        # Decoded images by (path, day): every upload gets a new name, so a known path is unchanged.
        self._decoded: dict[tuple[str, date], Decoded] = {}

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        posts: dict[date, str] = {}
        for path, day in await asyncio.to_thread(parse_news, (await http.get(NEWS_URL)).text()):
            if today <= day <= today + timedelta(days=1):
                posts.setdefault(day, path)  # the list is newest first
        schedules, decoded = {}, {}
        for day, path in sorted(posts.items()):
            image, uploaded, correction = await asyncio.to_thread(parse_post, (await http.get(BASE_URL + path)).text())
            if image is None:
                continue  # announced, the image is not there yet
            key = (image, day)
            if (result := self._decoded.get(key)) is None:
                png = (await http.get(BASE_URL + image)).body
                result = await asyncio.to_thread(parse_image, png, day)
            decoded[key] = result
            groups, unknown = result
            notes = (correction,) if correction else ()
            schedules[day] = DaySchedule(day, uploaded, groups, unknown, notes, image_url=BASE_URL + image)
        self._decoded = decoded
        return schedules
