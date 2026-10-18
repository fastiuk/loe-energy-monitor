"""Volynoblenergo: a PNG schedule from the inneti API at ``api-voe-poweron.inneti.net``.

::

    GET /api/options?option_key[]=pw_gpv_image_today&option_key[]=pw_gpv_image_tomorrow
        → {"hydra:member": [{"option_key": "pw_gpv_image_today",
                             "value": "/media/2026/10/6ad3c89bb9049_GPV.png"}, ...]}
    GET /api/archive_gpv_graphs?dateGraph[after]=2026-10-17
        → {"hydra:member": [{"id": 618, "dateGraph": "2026-10-18T00:00:00+00:00",
                             "dateCreate": "2026-10-17T22:12:27+00:00",
                             "imageUrl": {"contentUrl": "/media/2026/10/6ad3c89bb9049_GPV.png",
                                          "uploadTimestamp": "2026-10-17T19:12:27+00:00"}}, ...]}

The options name the images on show (``""`` when there is none). Which day an image is for
comes from the archive: "today" switches to the next day's image late in the evening, so the
key alone would date it wrong. ``dateGraph`` and ``dateCreate`` are Kyiv wall-clock times
labelled UTC; ``uploadTimestamp`` is real UTC.

The PNG is 1750×2250 with a fixed layout: two grids (00–12 and 12–24) with a header row and
12 rows for subqueues 1.1–6.2, each with 24 half-hour cells, light grey-blue with power and
dark blue without. The grids are found from the white lines between cells and checked
against the expected shape and colours before any cell is read.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, NamedTuple

from PIL import Image

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._image import decode_png
from .base import Provider, fail, url_on_host

log = logging.getLogger(__name__)

BASE_URL = "https://api-voe-poweron.inneti.net"
OPTION_KEYS = ("pw_gpv_image_today", "pw_gpv_image_tomorrow")
OPTIONS_URL = f"{BASE_URL}/api/options?" + "&".join(f"option_key[]={key}" for key in OPTION_KEYS)
ARCHIVE_URL = f"{BASE_URL}/api/archive_gpv_graphs"
HEADERS = {"Accept": "application/ld+json"}
MAX_MEMBERS = 100

GROUPS = tuple(f"{q}.{s}" for q in range(1, 7) for s in (1, 2))
SIZE = (1750, 2250)
COLUMNS = 24  # half-hour cells per grid, one grid per half of the day
ON_COLOR = (185, 200, 215)  # also the background of the group label cells
OFF_COLOR = (68, 110, 155)  # also the background of the header row
COLOR_TOLERANCE = 24
#: Luminance below this is table, above it is the white page and the lines between cells.
INK_LEVEL = 236
#: Pixels sampled in from a cell's left edge, clear of the centred header text.
EDGE = 3
#: Pixels trimmed from each side of a cell before reading its colour.
INSET = 6
UNRECOGNISED = "Нерозпізнаний графік"


class Version(NamedTuple):
    id: int
    day: date
    uploaded_at: datetime | None


Span = tuple[int, int]  # [start, end) in pixels
Decoded = tuple[dict[str, tuple[Outage, ...]], dict[str, str]]  # outages and unrecognised groups


def _members(payload: Any, what: str) -> list[Any]:
    members = payload.get("hydra:member") if isinstance(payload, dict) else None
    if not isinstance(members, list) or len(members) > MAX_MEMBERS:
        fail(f"Volyn: {what} response has no hydra:member list")
    return members


def parse_options(payload: Any) -> list[str]:
    """Paths of the images on show, today's first; empty when nothing is published."""
    values = {
        m["option_key"]: m.get("value")
        for m in _members(payload, "options")
        if isinstance(m, dict) and m.get("option_key") in OPTION_KEYS
    }
    if not values:
        fail("Volyn: no schedule image options")
    paths = []
    for key in OPTION_KEYS:
        value = values.get(key)
        if value in (None, ""):
            continue
        if url_on_host(BASE_URL, value) is None:
            fail(f"Volyn: {key} is not a plain path: {str(value)[:80]!r}")
        paths.append(value)
    return paths


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=KYIV)


def parse_archive(payload: Any) -> dict[str, Version]:
    """Archived images by path: the day each is for and when it was uploaded."""
    versions = {}
    for m in _members(payload, "archive"):
        if not isinstance(m, dict) or not isinstance(image := m.get("imageUrl"), dict):
            continue
        path, number = image.get("contentUrl"), m.get("id")
        graph = _datetime(m.get("dateGraph"))
        if not isinstance(path, str) or not isinstance(number, int) or graph is None:
            continue
        # Kyiv midnight, whether labelled UTC (as now) or given in real UTC: the same day in Kyiv.
        uploaded = _datetime(image.get("uploadTimestamp"))
        versions[path] = Version(number, graph.astimezone(KYIV).date(), uploaded and uploaded.astimezone(KYIV))
    return versions


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


def _even(spans: Sequence[Span], what: str) -> None:
    """Fail unless the spans have about the same size and follow each other closely."""
    sizes = [end - start for start, end in spans]
    gaps = [b[0] - a[1] for a, b in pairwise(spans)]
    if min(sizes) < 30 or max(sizes) - min(sizes) > 4 or any(gap > 8 for gap in gaps):
        fail(f"Volyn: uneven {what} in the schedule grid")


def _near(color: Sequence[int], reference: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= COLOR_TOLERANCE for a, b in zip(color, reference, strict=True))


def locate_grid(image: Image.Image) -> tuple[list[Span], list[list[Span]]]:
    """The 24 time columns and, for each half of the day, the 12 group rows."""
    width, height = image.size
    ink = image.convert("L").point(lambda v: 1 if v < INK_LEVEL else 0).tobytes()

    # Columns: cells are table colour from top to bottom of both grids, the lines between
    # them are white, so per-column ink counts separate them whatever the schedule says.
    counts = [sum(ink[x::width]) for x in range(width)]
    columns = _runs([count > max(counts) // 2 for count in counts])
    if len(columns) != COLUMNS + 2:  # "Черга", "Підчерга", then the time columns
        fail(f"Volyn: expected {COLUMNS + 2} table columns, found {len(columns)}")
    label, columns = columns[1], columns[2:]
    _even(columns, "columns")

    # Rows: inked at the left edge of the "Підчерга" cell and of every time cell. The
    # merged "Черга" cells have no line between subqueues, so that column is left out.
    xs = [label[0] + EDGE] + [left + EDGE for left, _ in columns]
    bands = _runs([all(ink[y * width + x] for x in xs) for y in range(height)])
    if len(bands) != 2 * (1 + len(GROUPS)):
        fail(f"Volyn: expected two grids of {1 + len(GROUPS)} rows, found {len(bands)} rows")
    halves = []
    for grid in (bands[: len(bands) // 2], bands[len(bands) // 2 :]):
        header, rows = grid[0], grid[1:]
        _even(rows, "rows")
        if rows[0][0] - header[1] > 8:
            fail("Volyn: header row is apart from the grid")
        middle = (header[0] + header[1]) // 2
        labels_ok = all(_near(image.getpixel((label[0] + EDGE, (top + end) // 2)), ON_COLOR) for top, end in rows)
        header_ok = all(_near(image.getpixel((left + EDGE, middle)), OFF_COLOR) for left, _ in columns)
        if not (labels_ok and header_ok):
            fail("Volyn: the schedule grid has unexpected colours")
        halves.append(rows)
    return columns, halves


def _slot(image: Image.Image, column: Span, row: Span) -> Slot | None:
    box = (column[0] + INSET, row[0] + INSET, column[1] - INSET, row[1] - INSET)
    colors = image.crop(box).getcolors(16)
    if not colors:
        return None
    count, color = max(colors)
    if count < 0.9 * (box[2] - box[0]) * (box[3] - box[1]):
        return None
    if _near(color, ON_COLOR):
        return Slot.ON
    if _near(color, OFF_COLOR):
        return Slot.OFF
    return None


def parse_image(png: bytes, day: date) -> Decoded:
    """Outages by group from one schedule PNG, and the groups whose cells were unrecognised."""
    image = decode_png(png, SIZE, "Volyn")
    columns, halves = locate_grid(image)
    groups: dict[str, tuple[Outage, ...]] = {}
    unknown: dict[str, str] = {}
    for i, group in enumerate(GROUPS):
        slots = [_slot(image, column, rows[i]) for rows in halves for column in columns]
        if None in slots:
            unknown[group] = UNRECOGNISED
        else:
            groups[group] = outages_from_slots(day, slots)  # type: ignore[arg-type]
    return groups, unknown


class Volyn(Provider):
    hosts = frozenset({"api-voe-poweron.inneti.net"})
    min_interval = 300

    def __init__(self) -> None:
        # Decoded images by (path, upload time, day). Every upload gets a new file name, so
        # an unchanged path means an unchanged image: no need to download it every poll.
        self._decoded: dict[tuple[str, datetime | None, date], Decoded] = {}

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        paths = parse_options((await http.get(OPTIONS_URL, headers=HEADERS)).json())
        if not paths:
            return {}
        since = now.astimezone(KYIV).date() - timedelta(days=1)
        resp = await http.get(ARCHIVE_URL, params={"dateGraph[after]": since.isoformat()}, headers=HEADERS)
        versions = parse_archive(resp.json())

        latest: dict[date, tuple[str, Version]] = {}
        for path in paths:
            if (version := versions.get(path)) is None:
                # An image for an older day still on show, or one not archived yet.
                log.info("Volyn: %s is not in the archive since %s, skipping it", path, since)
                continue
            if version.day not in latest or latest[version.day][1].id < version.id:
                latest[version.day] = (path, version)

        schedules, decoded = {}, {}
        for day, (path, version) in sorted(latest.items()):
            key = (path, version.uploaded_at, day)
            if (result := self._decoded.get(key)) is None:
                result = parse_image((await http.get(BASE_URL + path)).body, day)
            decoded[key] = result
            groups, unknown = result
            schedules[day] = DaySchedule(day, version.uploaded_at, groups, unknown, image_url=BASE_URL + path)
        self._decoded = decoded
        return schedules
