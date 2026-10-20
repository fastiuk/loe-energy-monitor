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

The group of an address comes from the workbooks the operator publishes for that purpose on
``/page/pogodinni-vidkljuchennja`` («Файли співставлення адрес та черг»): one
``/Content/Uploads/GPV6/xls/Черги_на_відключення_Летичівський_РЕМ_09102026.xlsx`` per РЕМ (the date
is in the name), Хмельницький РЕМ as two, ``…_побут_…`` and ``…_непобут_…``. A title row
(«Летичівський РЕМ»), then «Населений пункт | Вулиця | Список будинків | Черга/підчерга» and a row
per street and queue: «Загінці | вул. Берегова | 1, 10, 4/1 | 1.1. підчерга». A house in several
rows has several connections, so several queues. The site's own search (``/settlements`` …)
answers only its page's AJAX requests and is not used.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import unquote

from PIL import Image

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._address import clean_label, natural_key, normalize_group, pick, search_term
from ._html import Node, parse_html
from ._image import decode_png
from ._text import MONTHS
from ._xlsx import MAX_BYTES as MAX_WORKBOOK
from ._xlsx import read_sheet
from .base import AddressLookup, Choice, Provider, Resolution, SourceFormatError, Step, fail

BASE_URL = "https://hoe.com.ua"
NEWS_URL = BASE_URL + "/post/novini-kompaniji"
QUEUES_URL = BASE_URL + "/page/pogodinni-vidkljuchennja"
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
_POSTED_MONTHS = {name: number for number, name in enumerate(_SHORT_MONTHS, 1)}
_POSTED_RE = re.compile(r"(\d{1,2})\s{1,3}(" + "|".join(_SHORT_MONTHS) + r")\w{0,8}\.?\s{1,3}(\d{4})", re.IGNORECASE)
_POST_PATH_RE = re.compile(r"/post/[A-Za-z0-9_-]{1,160}\.html")
_IMAGE_RE = re.compile(r"/Content/Uploads/\d{4}/\d{2}/file(\d{17})\.png", re.IGNORECASE)
_CORRECTION_RE = re.compile(r"підчерг", re.IGNORECASE)

Span = tuple[int, int]
Decoded = tuple[dict[str, tuple[Outage, ...]], dict[str, str]]


def parse_news(markup: str) -> list[tuple[str, date]]:
    """(post path, schedule day) for every schedule post in the news list, newest first."""
    root = parse_html(markup)
    # Items inside items are not news: reading each one's text again would be quadratic.
    articles = root.find_all("article", cls="item", nested=False)
    if not articles:
        fail("Khmelnytskyi: no news items on the page")
    posts = []
    for article in articles:
        heading = article.find(cls="heading")
        link = heading.find("a") if heading else None
        if link is None or not _POST_PATH_RE.fullmatch(link.attrs.get("href", "")):
            continue
        if (title := _TITLE_RE.search(link.text(300))) is None:
            continue
        if (posted := _POSTED_RE.search(article.text(2000))) is None:
            continue
        # IGNORECASE also matches look-alikes .lower() keeps (U+1C82 for "о"): not a month.
        month, posted_month = MONTHS.get(title[2].lower()), _POSTED_MONTHS.get(posted[2].lower())
        if month is None or posted_month is None:
            continue
        day = int(title[1])
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
        # Decoded images (or why they could not be read) by (path, day): every upload gets a
        # new name, so a known path is unchanged.
        self._decoded: dict[tuple[str, date], Decoded | str] = {}

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        today = now.astimezone(KYIV).date()
        posts: dict[date, str] = {}
        for path, day in await asyncio.to_thread(parse_news, (await http.get(NEWS_URL)).text()):
            if today <= day <= today + timedelta(days=1):
                posts.setdefault(day, path)  # the list is newest first
        schedules, decoded, errors = {}, {}, []
        for day, path in sorted(posts.items()):
            image, uploaded, correction = await asyncio.to_thread(parse_post, (await http.get(BASE_URL + path)).text())
            if image is None:
                continue  # announced, the image is not there yet
            key = (image, day)
            if (result := self._decoded.get(key)) is None:
                png = (await http.get(BASE_URL + image)).body
                try:
                    result = await asyncio.to_thread(parse_image, png, day)
                except SourceFormatError as err:
                    # The message only: the exception's traceback holds the decoder's frames.
                    result = str(err)
            decoded[key] = result
            if isinstance(result, str):
                errors.append(result)
                continue
            groups, unknown = result
            notes = (correction,) if correction else ()
            schedules[day] = DaySchedule(day, uploaded, groups, unknown, notes, image_url=BASE_URL + image)
        self._decoded = decoded
        if errors:
            fail(errors[0])
        return schedules


#: The lookup reads the workbooks again after this long; a new version comes under a new name.
FILES_TTL = 3600
#: Real numbers: 7 workbooks, 22 000 rows in all, at most 9 rows a street and 450 houses a row.
MAX_FILES = 12
MAX_ROWS = 100_000
MAX_STREET_ROWS = 50
MAX_HOUSES = 2000
_HTML = {"Accept": "text/html"}
_HEADERS = ("населений пункт", "вулиця", "список будинків", "черга/підчерга")
_FILE_RE = re.compile(
    r"/Content/Uploads/[A-Za-z0-9_-]{1,40}/xls/Черги_на_відключення_(\w{1,60}?)_РЕМ(?:_(побут|непобут))?_([0-9]{8})\.xlsx"
)
_QUEUE_RE = re.compile(r"([0-9]{1,2})\s{0,3}[.,]\s{0,3}([0-9]{1,2})\.?\s{0,3}(?:під)?черга", re.IGNORECASE)
#: "п.1", "під.1", "кв.4", "корп.1": a part of the house named before the comma ("18/2,п.1").
_PART_RE = re.compile(r"(?:п|під|кв|корп|прим)\.?\s{0,2}[0-9]", re.IGNORECASE)
_QUOTES_RE = re.compile("[\"'`’ʼ]")
_SLASH_RE = re.compile(r"\s{0,3}[/\\]\s{0,3}")
#: "10-а", "1 А", "115-А/2": the letter of a house number, written "10А".
_LETTER_RE = re.compile(r"(?<=[0-9])[\s-]{0,3}([^\W\d_])(?=/|$)")
_DIGIT_RE = re.compile(r"[0-9]")
#: "8A" typed with a Latin letter is "8А".
_LATIN = str.maketrans("ABCEHIKMOPTX", "АВСЕНІКМОРТХ")
#: "скверФранка", "хутірГуляк": the kind of street glued to its name.
_GLUED_RE = re.compile(r"^(сквер|масив|майдан|хутір|шосе|бульвар|площа|провулок|вулиця)(?=[А-ЯІЇЄҐ])")

#: Settlement, street, the list of houses as written, queue ("5.2", or the text when it is none).
Row = tuple[str, str, str, str]
#: The list of houses, the queue and whether the workbook is of household consumers.
Entry = tuple[str, str, bool]


@dataclass(frozen=True)
class Place:
    """A settlement of one РЕМ and its streets."""

    name: str
    streets: dict[str, list[Entry]]


def parse_queue_files(page: str) -> list[tuple[str, str, bool]]:
    """(path, РЕМ, household) for the newest queue workbook of each РЕМ the page links."""
    body = parse_html(page).find("div", cls="post")
    if body is None:
        fail("Khmelnytskyi: the queues page has no body")
    newest: dict[tuple[str, bool], tuple[str, str]] = {}
    for link in body.find_all("a"):
        href = unquote(link.attrs.get("href", "")[:600]).removeprefix(BASE_URL)
        if not (m := _FILE_RE.fullmatch(href)):
            continue
        key = (m[1], m[2] != "непобут")
        stamp = m[3][4:] + m[3][2:4] + m[3][:2]  # DDMMYYYY → YYYYMMDD
        if key not in newest or stamp > newest[key][0]:
            newest[key] = (stamp, href)
    if not newest or len(newest) > MAX_FILES:
        fail(f"Khmelnytskyi: expected the queue workbooks on the page, found {len(newest)}")
    files = [(href, rem, household) for (rem, household), (_, href) in newest.items()]
    return sorted(files, key=lambda f: (f[1], not f[2]))  # households first: their title names the РЕМ


def _queue(text: str) -> str | None:
    """ "5.2. підчерга" → "5.2"; other text stays as it is (cleaned)."""
    if m := _QUEUE_RE.fullmatch(text.strip()):
        return normalize_group(f"{m[1]}.{m[2]}")
    return normalize_group(text) or clean_label(text, 40)


def tidy_street(name: str) -> str:
    """ "скверФранка" → "сквер Франка"."""
    return _GLUED_RE.sub(r"\1 ", name)


def parse_queue_file(data: bytes) -> tuple[str | None, list[Row]]:
    """The РЕМ a workbook's title names and its rows."""
    rows = read_sheet(data, "Khmelnytskyi queues")
    for start, cells in enumerate(rows[:20]):
        names = [c.strip().casefold() for c in cells]
        if all(h in names for h in _HEADERS):
            columns = [names.index(h) for h in _HEADERS]
            break
    else:
        fail("Khmelnytskyi: no header row in the queue workbook")
    title = next((c for cells in rows[:start] for c in cells if c.strip()), "")
    entries = []
    for cells in rows[start + 1 :]:
        if len(cells) <= max(columns):
            continue
        settlement, street, houses, queue = (cells[c] for c in columns)
        settlement, street, value = clean_label(settlement, 60), clean_label(street, 60), _queue(queue)
        if settlement and street and value and houses.strip():
            entries.append((settlement, tidy_street(street), houses, value))
    return clean_label(title.split("(")[0], 60), entries


def build_places(workbooks: Iterable[tuple[str, bool, bytes]]) -> dict[str, Place]:
    """Settlements by label, "Деражня (Летичівський РЕМ)", from every РЕМ's workbooks."""
    titles: dict[str, str] = {}
    places: dict[str, Place] = {}
    total = 0
    for rem, household, data in workbooks:
        title, rows = parse_queue_file(data)
        if (total := total + len(rows)) > MAX_ROWS:
            fail(f"Khmelnytskyi: over {MAX_ROWS} rows in the queue workbooks")
        name = titles.setdefault(rem, title or clean_label(rem.replace("_", "-") + " РЕМ", 60) or "РЕМ")
        for settlement, street, houses, queue in rows:
            label = clean_label(f"{settlement} ({name})")
            assert label is not None
            entries = places.setdefault(label, Place(settlement, {})).streets.setdefault(street, [])
            if len(entries) >= MAX_STREET_ROWS:
                fail(f"Khmelnytskyi: over {MAX_STREET_ROWS} rows for one street")
            entries.append((houses, queue, household))
    return places


def house_label(item: str) -> str | None:
    """A house as it is shown: "10-а" → "10А", "53\\1" → "53/1"; ``None`` without a number."""
    text = clean_label(_QUOTES_RE.sub("", item), 40)
    if not text or not _DIGIT_RE.search(text):
        return None
    text = _LETTER_RE.sub(lambda m: m[1].upper().translate(_LATIN), _SLASH_RE.sub("/", text))
    return text.rstrip(" .-") or None


def split_houses(listed: str) -> list[str]:
    """ "1, 2а, 18/2,п.1" → ["1", "2А", "18/2", "18/2 п.1"]."""
    labels, house = [], ""
    for item in listed.split(",", MAX_HOUSES)[:MAX_HOUSES]:
        item = item.strip()
        if house and _PART_RE.match(item):
            item = f"{house} {item}"
        else:
            house = item
        if label := house_label(item):
            labels.append(label)
    return labels


def houses(entries: Iterable[Entry]) -> dict[str, list[tuple[str, bool]]]:
    """House → (queue, household) for each row of the street that lists it."""
    found: dict[str, list[tuple[str, bool]]] = {}
    for listed, queue, household in entries:
        for house in split_houses(listed):
            found.setdefault(house, []).append((queue, household))
    return found


def resolution(queues: Iterable[tuple[str, bool]]) -> Resolution:
    """Queues of household consumers if the house has any, else of everyone in it."""
    queues = list(queues)
    counts = Counter(q for q, household in queues if household) or Counter(q for q, _ in queues)
    ordered = sorted(counts, key=lambda q: (-counts[q], natural_key(q)))
    groups = tuple(q for q in ordered if normalize_group(q))
    return Resolution(groups, tuple(q for q in ordered if q not in groups))


def _get[T](items: dict[str, T], key: str, what: str) -> T:
    if not isinstance(key, str) or key not in items:
        raise ValueError(f"no such {what}")
    return items[key]


class KhmelnytskyiLookup(AddressLookup):
    """Settlement, street and house in the operator's workbooks, read once and searched in memory.

    Reading them all takes a request for the page and one per workbook (about 1.2 MB in all), so
    the lookup keeps what it read (about 10 MB in memory) for ``FILES_TTL``, then lets it go even
    if nobody searches again.
    """

    hosts = frozenset({"hoe.com.ua"})
    max_bytes = MAX_WORKBOOK
    steps = (
        Step("Напишіть назву населеного пункту", "Хмельницький"),
        Step("Напишіть назву вулиці", "Проскурівська", browse=True),
        Step("Напишіть номер будинку", "11А", browse=True, house=True),
    )

    def __init__(self) -> None:
        self._places: dict[str, Place] | None = None
        self._expires = 0.0

    async def _load(self, http: Fetcher) -> dict[str, Place]:
        if self._places is not None and time.monotonic() < self._expires:
            return self._places
        files = await asyncio.to_thread(parse_queue_files, (await http.get(QUEUES_URL, headers=_HTML)).text())
        workbooks = [(rem, household, (await http.get(BASE_URL + path)).body) for path, rem, household in files]
        places = await asyncio.to_thread(build_places, workbooks)
        self._places, self._expires = places, time.monotonic() + FILES_TTL
        asyncio.get_running_loop().call_later(FILES_TTL, self._forget, places)
        return places

    def _forget(self, places: dict[str, Place]) -> None:
        if self._places is places:
            self._places = None

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        if len(path) >= len(self.steps):
            raise ValueError("the address is complete")
        if not path and len(search_term(query)) < self.min_query:
            return []
        places = await self._load(http)
        if not path:
            # Matched by the settlement's name alone: every place of a РЕМ has its name in the label.
            return [Choice(c.id, c.id) for c in pick((Choice(k, p.name) for k, p in places.items()), query)]
        streets = _get(places, path[0], "settlement").streets
        if len(path) == 1:
            return pick((Choice(s, s) for s in streets), query)
        return pick((Choice(h, h) for h in houses(_get(streets, path[1], "street"))), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        streets = _get(await self._load(http), path[0], "settlement").streets
        return resolution(_get(houses(_get(streets, path[1], "street")), path[2], "house"))
