"""Vinnytsiaoblenergo: the hourly table of one address on ``https://www.voe.com.ua/disconnection/detailed``.

The page is a Drupal form that the site fills from three open autocompletes. Submitting it is a
plain POST of the form every visitor gets (no captcha element on it, no cookies needed)::

    GET  /disconnection/detailed
         → form#disconnection-detailed-search-form with hidden form_build_id ("form-…", the
           same for every visitor while the page is cached) and form_id
    POST /disconnection/detailed  search_type=0 city_id=510100000 street_id=1524 house_id=640806
         city= street= house= (the text fields may stay empty) form_build_id=… form_id=… search=Показати
    → the page again, with the address's ``div.disconnection-detailed-table``

The table holds ``<p>1.1 черга</p>`` only while ГПВ apply (in normal mode there is no queue and
every cell is ``no_disconnection``), a message, and ``div.disconnection-detailed-table-container``:
24 ``head`` cells ("00-01" … "23-24"), then per day a ``day_col`` cell ("вт 20.10", without a
year; ``current_day`` today, ``other_day`` later) followed by its 24 hour cells:

- ``no_disconnection`` with an empty ``div.hour_block`` — power on;
- ``has_disconnection full_hour`` — off for the whole hour;
- a ``div.fill`` with ``style="--start: 50; --size: 50"`` in the ``hour_block`` — off for that
  part of the hour, in percent (the legend speaks of the first or the second 30 minutes);
- ``confirmed``, or ``confirm_N`` with N ≥ 3, on the cell or the fill — a confirmed outage;
  ``confirm_N`` with N ≤ 2 — a possible one. Until February 2026 ``confirm_1`` meant confirmed;
  in October 2026 the page had no outages to check the current meaning against.

The address directory is open: ``/disconnection/detailed/autocomplete/read_city?q=``,
``read_street/{city_id}?q=`` and ``read_house/{street_id}?q=`` answer
``[{"value": "вулиця. Соборна", "label": "<div class=\\"change_api_autocomplit\\"
data-method=\\"read_street\\" data-id=\\"1652\\">…</div>"}]``. Settlements and streets match a
case-insensitive substring; houses match a prefix, 10 at most.

Never used: ``disconnection.voe.com.ua`` (behind a Cloudflare challenge) and the form's search by
EIC code or personal account (``search_type`` 1 and 2).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, at_minute, merge_outages
from ._address import (
    MAX_LABEL,
    clean_label,
    clean_query,
    entries,
    normalize_group,
    numeric_id,
    pick,
    require_id,
    search_term,
    strip_kind,
)
from ._html import Node, parse_html
from .base import AddressLookup, AddressSource, Choice, Resolution, SourceBlocked, SourceFormatError, Step, fail

BASE_URL = "https://www.voe.com.ua"
FORM_URL = f"{BASE_URL}/disconnection/detailed"
AUTOCOMPLETE_URL = f"{FORM_URL}/autocomplete"
HOSTS = frozenset({"www.voe.com.ua"})
FORM_ID = "disconnection_detailed_search_form"
#: ``confirm_N`` from this level up marks a confirmed outage, below it a possible one.
CONFIRMED_LEVEL = 3
#: Seconds the form's hidden fields are reused; the site itself caches the page for hours.
FORM_TTL = 600
MAX_TABLES = 4
MAX_DAYS = 7
MAX_FILLS = 4
MAX_SUGGESTIONS = 100
UNKNOWN = "Нерозпізнаний графік"
NOT_APPLIED = (
    "Вінницяобленерго показує чергу адреси лише тоді, коли діють графіки погодинних відключень, "
    "а зараз вони не діють. Спробуйте, коли графіки введуть."
)
_HTML = {"Accept": "text/html"}

#: "<city_id>/<street_id>/<house_id>": the directory's ids, nothing else.
_ADDRESS_RE = re.compile(r"([0-9]{1,12})/([0-9]{1,12})/([0-9]{1,12})")
_BUILD_ID_RE = re.compile(r"form-[A-Za-z0-9_-]{20,100}")
_DAY_RE = re.compile(r"([0-9]{1,2})\.([0-9]{1,2})")
#: "1.1 черга", or "Черга 1.1".
_QUEUE_RE = re.compile(
    r"([0-9]{1,2})\s{0,3}[.,]\s{0,3}([0-9]{1,2})\s{0,3}черг"
    r"|черга\s{0,3}№?\s{0,3}([0-9]{1,2})\s{0,3}[.,]\s{0,3}([0-9]{1,2})",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(r"confirm_([0-9]{1,2})")
_START_RE = re.compile(r"--start\s{0,3}:\s{0,3}([0-9]{1,3}(?:\.[0-9]{1,4})?)")
_SIZE_RE = re.compile(r"--size\s{0,3}:\s{0,3}([0-9]{1,3}(?:\.[0-9]{1,4})?)")
_CHALLENGE = ("Just a moment", "challenge-platform", "cf-chl")
_DOTS_RE = re.compile(r"\.{2,}")
#: "вулиця. Соборна": a full word for the kind of street needs no dot ("м. Вінниця" keeps it).
_KIND_DOT_RE = re.compile(r"^(\w{6,})\.(?=\s)")
_HOUSE_JUNK_RE = re.compile(r"[\s№-]+")


def address_key(city: str, street: str, house: str) -> str | None:
    key = f"{city}/{street}/{house}"
    return key if _ADDRESS_RE.fullmatch(key) else None


def _search_form(root: Node, page: str) -> Node:
    form = root.find("form", id="disconnection-detailed-search-form")
    if form is None:
        if any(marker in page[:20_000] for marker in _CHALLENGE):
            raise SourceBlocked("Vinnytsia: the page is behind a Cloudflare challenge")
        fail("Vinnytsia: no address form on the page")
    return form


def parse_form(page: str) -> dict[str, str]:
    """The hidden fields the site gives every visitor with the form."""
    form = _search_form(parse_html(page), page)
    hidden = {
        node.attrs.get("name"): node.attrs.get("value", "")
        for node in form.find_all("input")
        if node.attrs.get("type") == "hidden"
    }
    build_id = hidden.get("form_build_id", "")
    if hidden.get("form_id") != FORM_ID or not _BUILD_ID_RE.fullmatch(build_id):
        fail("Vinnytsia: the address form has changed")
    return {"form_build_id": build_id, "form_id": FORM_ID}


def _day(label: str, today: date) -> date:
    """ "вт 20.10" → the date nearest to ``today`` (the table writes no year)."""
    if not (m := _DAY_RE.search(label[:40])):
        fail(f"Vinnytsia: a day without a date {label[:40]!r}")
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, int(m[2]), int(m[1])))
        except ValueError:
            pass
    day = min(candidates, key=lambda d: abs((d - today).days), default=None)
    if day is None or abs((day - today).days) > MAX_DAYS:
        fail(f"Vinnytsia: an unexpected day {label[:40]!r}")
    return day


def _possible(classes: set[str]) -> bool | None:
    """Whether an outage is only possible; ``None`` when the classes don't say."""
    if "confirmed" in classes:
        return False
    levels = [int(m[1]) for c in classes if (m := _CONFIRM_RE.fullmatch(c))]
    return max(levels) < CONFIRMED_LEVEL if levels else None


def _fill_span(style: str) -> tuple[int, int] | None:
    """ "--start: 50; --size: 50" → minutes 30 to 60 of the hour."""
    style = style[:200]
    if not (start := _START_RE.search(style)) or not (size := _SIZE_RE.search(style)):
        return None
    first, length = float(start[1]), float(size[1])
    if first + length > 100.001:
        return None
    begin, end = round(first * 0.6), round((first + length) * 0.6)
    return (begin, end) if begin < end else None


def _half(classes: set[str]) -> tuple[int, int] | None:
    if any(c.endswith(("first_half", "first-half")) for c in classes):
        return 0, 30
    if any(c.endswith(("second_half", "second-half")) for c in classes):
        return 30, 60
    return None


def _cell(cell: Node) -> list[tuple[int, int, bool]] | None:
    """Off spans of one hour cell as (from, to, possible) in minutes; ``None`` if unrecognised."""
    classes = set(cell.classes)
    for child in cell.children:
        if isinstance(child, Node) and "hour_block" in child.classes:
            classes.update(child.classes)
    off = "has_disconnection" in classes
    if off and "no_disconnection" in classes:
        return None
    default = bool(_possible(classes))
    if "full_hour" in classes:
        return [(0, 60, default)] if off else None
    fills = [n for n in cell.iter() if n is not cell and "fill" in n.classes]
    if len(fills) > MAX_FILLS:
        return None
    if fills:
        spans = []
        for fill in fills:
            if (span := _fill_span(fill.attrs.get("style", ""))) is None:
                return None
            own = _possible(set(fill.classes))
            spans.append((*span, default if own is None else own))
        return spans
    if half := _half(classes):
        return [(*half, default)]
    if off:
        return [(0, 60, default)]
    return [] if "no_disconnection" in classes else None


def _outages(day: date, cells: list[Node]) -> tuple[Outage, ...] | None:
    if len(cells) != 24:
        return None
    outages = []
    for hour, cell in enumerate(cells):
        if (spans := _cell(cell)) is None:
            return None
        outages += [Outage(at_minute(day, hour * 60 + a), at_minute(day, hour * 60 + b), p) for a, b, p in spans]
    return merge_outages(outages)


def _queue(table: Node) -> str | None:
    """ "<p>1.1 черга</p>" right in the table (not in its message)."""
    for child in table.children:
        if isinstance(child, Node) and child.tag == "p" and (m := _QUEUE_RE.search(child.text()[:80])):
            return normalize_group(f"{m[1] or m[3]}.{m[2] or m[4]}")
    return None


def _columns(container: Node, today: date) -> list[tuple[date, list[Node]]]:
    columns: list[tuple[date, list[Node]]] = []
    for node in container.children:
        if not isinstance(node, Node) or "disconnection-detailed-table-cell" not in node.classes:
            continue
        if "day_col" in node.classes:
            day = _day(node.text(), today)
            if len(columns) >= MAX_DAYS or any(d == day for d, _ in columns):
                fail("Vinnytsia: too many or repeated days in the table")
            columns.append((day, []))
        elif "cell" in node.classes and "head" not in node.classes:
            if not columns:
                fail("Vinnytsia: hour cells before the first day")
            columns[-1][1].append(node)
    if not columns:
        fail("Vinnytsia: the table has no days")
    return columns


def _tables(page: str) -> list[Node]:
    tables = _search_form(parse_html(page), page).find_all("div", cls="disconnection-detailed-table")
    if not tables or len(tables) > MAX_TABLES:
        fail("Vinnytsia: no schedule table on the page")
    return tables


def parse(page: str, today: date) -> dict[date, DaySchedule]:
    """The schedule of the address the page was asked for; ``{}`` when no queue is shown."""
    days: dict[date, tuple[dict[str, tuple[Outage, ...]], dict[str, str]]] = {}
    for table in _tables(page):
        container = table.find("div", cls="disconnection-detailed-table-container")
        if container is None:
            fail("Vinnytsia: a table without cells")
        columns = _columns(container, today)
        group = _queue(table)
        if group is None:
            # Normal mode: no queue and nothing off. Outages without a queue mean the page changed.
            if any(_outages(day, cells) != () for day, cells in columns):
                fail("Vinnytsia: outages without a queue")
            continue
        for day, cells in columns:
            groups, unknown = days.setdefault(day, ({}, {}))
            if (outages := _outages(day, cells)) is None:
                unknown[group] = UNKNOWN
            else:
                groups[group] = outages
    return {day: DaySchedule(day, None, groups, unknown) for day, (groups, unknown) in sorted(days.items())}


def parse_queues(page: str) -> list[str]:
    """The queues the page shows for the address (none in normal mode)."""
    queues: list[str] = []
    for table in _tables(page):
        if (group := _queue(table)) and group not in queues:
            queues.append(group)
    return queues


class _AddressPage:
    """Asks the form about one address the way the page does; keeps the form's hidden fields."""

    def __init__(self) -> None:
        self._form: dict[str, str] | None = None
        self._expires = 0.0

    def forget(self) -> None:
        self._form = None

    async def fetch(self, http: Fetcher, address: str) -> str:
        m = _ADDRESS_RE.fullmatch(address) if isinstance(address, str) else None
        if m is None:
            raise ValueError("not a Vinnytsia address key")
        if self._form is None or time.monotonic() >= self._expires:
            page = (await http.get(FORM_URL, headers=_HTML)).text()
            self._form = await asyncio.to_thread(parse_form, page)
            self._expires = time.monotonic() + FORM_TTL
        data = {
            "search_type": "0",
            "city": "",
            "city_id": m[1],
            "street": "",
            "street_id": m[2],
            "house": "",
            "house_id": m[3],
            **self._form,
            "search": "Показати",
        }
        return (await http.post(FORM_URL, data=data, headers=_HTML)).text()


class Vinnytsia(AddressSource):
    hosts = HOSTS
    ukraine_only = True  # 403 from GitHub's runners; answers Ukrainian connections, datacentres too
    #: Two requests per address and a 140 KB page each time: no need to ask more often.
    min_interval = 600
    request_gap = 2.0

    def __init__(self) -> None:
        self._page = _AddressPage()

    def valid_address(self, address: str) -> bool:
        return isinstance(address, str) and _ADDRESS_RE.fullmatch(address) is not None

    async def fetch_address(self, http: Fetcher, now: datetime, address: str) -> dict[date, DaySchedule]:
        if not self.valid_address(address):
            raise ValueError("not a Vinnytsia address key")
        page = await self._page.fetch(http, address)
        try:
            return await asyncio.to_thread(parse, page, now.astimezone(KYIV).date())
        except SourceFormatError:
            self._page.forget()  # perhaps the form changed: ask for it again next time
            raise


def tidy(label: str) -> str:
    """ "м.. Вінниця" → "м. Вінниця", "вулиця. Соборна" → "вулиця Соборна"."""
    return _KIND_DOT_RE.sub(r"\1", _DOTS_RE.sub(".", label))


def house_term(query: str) -> str:
    """ "буд. 51-а" → "51А": the directory writes "51А", "53/5" and matches by prefix."""
    return _HOUSE_JUNK_RE.sub("", strip_kind(clean_query(query))).upper()[:20]


def parse_choices(payload: Any, method: str) -> list[Choice]:
    """``[{"value": "вулиця. Соборна", "label": "<div class=\\"change_api_autocomplit\\" …>"}]`` → choices.

    Only entries of this step (``data-method``) with a numeric ``data-id`` count.
    """
    limit = 30 if method == "read_house" else MAX_LABEL
    choices = []
    for item in entries(payload, "Vinnytsia addresses")[:MAX_SUGGESTIONS]:
        markup = item.get("label") if isinstance(item, dict) else None
        if not isinstance(markup, str) or len(markup) > 2000:
            continue
        node = parse_html(markup).find(cls="change_api_autocomplit")
        if node is None or node.attrs.get("data-method") != method:
            continue
        if not (id_ := numeric_id(node.attrs.get("data-id"))):
            continue
        value = item.get("value")
        if name := clean_label(value if isinstance(value, str) else node.text(), limit):
            choices.append(Choice(id_, tidy(name)))
    return choices


class VinnytsiaLookup(AddressLookup):
    """Settlement, street and house from the form's autocompletes; the queue from the form itself.

    The page shows the queue only while ГПВ apply: in normal mode ``resolve`` finds none.
    """

    hosts = HOSTS
    request_gap = 1.0
    ukraine_only = True
    steps = (
        Step("Напишіть назву населеного пункту", "Вінниця"),
        Step("Напишіть назву вулиці", "Соборна"),
        Step("Напишіть номер будинку", "2", house=True),
    )

    def __init__(self) -> None:
        self._page = _AddressPage()

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        ids = [require_id(p) for p in path]
        if not ids:
            method, url, term = "read_city", f"{AUTOCOMPLETE_URL}/read_city", search_term(query)
        elif len(ids) == 1:
            method, url, term = "read_street", f"{AUTOCOMPLETE_URL}/read_street/{ids[0]}", search_term(query)
        elif len(ids) == 2:
            method, url, term = "read_house", f"{AUTOCOMPLETE_URL}/read_house/{ids[1]}", house_term(query)
        else:
            raise ValueError("the address is complete")
        if len(term) < (1 if method == "read_house" else self.min_query):
            return []
        resp = await http.get(url, params={"q": term}, headers={"Accept": "application/json"})
        return pick(parse_choices(resp.json(), method), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        address = address_key(*(require_id(p) for p in path))
        if address is None:
            raise ValueError("not a Vinnytsia address")
        page = await self._page.fetch(http, address)
        try:
            queues = await asyncio.to_thread(parse_queues, page)
        except SourceFormatError:
            self._page.forget()
            raise
        return Resolution(tuple(queues), (), address, reason=None if queues else NOT_APPLIED)
