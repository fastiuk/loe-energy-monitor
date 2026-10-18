"""Zhytomyroblenergo: the coloured table on ``https://www.ztoe.com.ua/unhooking-search.php``.

The page (windows-1251) says "Дата оновлення інформації - 16:30 18.10.2026", then per day a
header cell ``<td colspan="48"><b>18.10.2026</b></td>`` and a row per subqueue: a link
``pidcherga_id=N`` labelled "1.1" and 48 half-hour cells, ``background: #ffffff`` with power
and ``#ff3333`` without.

The same page finds the group of an address with GET parameters: ``?rem_id=`` fills the
settlements select, ``&naspunkt_id=`` the streets, and ``&vulica_id=…&all=1`` lists every row
for the street: settlement, street, houses ("49, 55, 53"), queue, subqueue and a note
("побутові споживачі" or "непобутові (юридичні) споживачі").
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date, datetime

from ..http import Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from ._address import MAX_ENTRIES, clean_label, numeric_id, pick, require_id
from ._html import Node, parse_html
from .base import AddressLookup, Choice, Provider, Resolution, Step, fail

URL = "https://www.ztoe.com.ua/unhooking-search.php"
COLORS = {"#ffffff": Slot.ON, "#ff3333": Slot.OFF}
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_UPDATED_RE = re.compile(r"Дата оновлення інформації\s{0,5}-\s{0,5}(\d{2}):(\d{2})\s{1,5}(\d{2})\.(\d{2})\.(\d{4})")
_GROUP_RE = re.compile(r"\d\.\d")
_BACKGROUND_RE = re.compile(r"background:\s{0,3}(#[0-9a-fA-F]{6})")


def _cells(row: Node) -> list[Node]:
    return [c for c in row.children if isinstance(c, Node) and c.tag == "td"]


def parse(page: str) -> dict[date, DaySchedule]:
    root = parse_html(page)
    updated = None
    if m := _UPDATED_RE.search(root.text()):
        try:
            updated = datetime(int(m[5]), int(m[4]), int(m[3]), int(m[1]), int(m[2]), tzinfo=KYIV)
        except ValueError:
            pass

    days: dict[date, tuple[dict[str, tuple[Outage, ...]], dict[str, str]]] = {}
    day: date | None = None
    for row in root.find_all("tr"):
        cells = _cells(row)
        header = next((c for c in cells if c.attrs.get("colspan") == "48"), None)
        if header is not None and (m := _DATE_RE.fullmatch(header.text())):
            try:
                day = date(int(m[3]), int(m[2]), int(m[1]))
            except ValueError:
                fail(f"Zhytomyr: invalid date {header.text()!r}")
            days.setdefault(day, ({}, {}))
            continue
        label = next(
            (
                c.text()
                for c in cells
                if _GROUP_RE.fullmatch(c.text()) and any("pidcherga_id" in a.attrs.get("href", "") for a in c.find_all("a"))
            ),
            None,
        )
        if label is None or day is None:
            continue
        colors = [m[1].lower() for c in cells if (m := _BACKGROUND_RE.search(c.attrs.get("style", "")))]
        groups, unknown = days[day]
        states = [COLORS.get(color) for color in colors[-48:]]
        if len(states) != 48 or None in states:
            unknown[label] = "Нерозпізнаний графік"
        else:
            groups[label] = outages_from_slots(day, states)  # type: ignore[arg-type]
    if not days:
        fail("Zhytomyr: no schedule tables on the page")
    return {
        d: DaySchedule(d, updated, groups, unknown)
        for d, (groups, unknown) in sorted(days.items())
        if groups or unknown
    }


class Zhytomyr(Provider):
    hosts = frozenset({"www.ztoe.com.ua"})
    min_interval = 300

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(URL)
        return parse(resp.text("cp1251"))


_SELECTS = ("rem_id", "naspunkt_id", "vulica_id")
HOUSEHOLD = "побутові"


def parse_select(page: str, name: str) -> list[Choice]:
    """Options of ``<select name=…>`` on the search page, without the "Оберіть…" placeholder."""
    select = next((s for s in parse_html(page).find_all("select") if s.attrs.get("name") == name), None)
    if select is None:
        fail(f"Zhytomyr: no {name} list on the page")
    choices = []
    for option in select.find_all("option")[:MAX_ENTRIES]:
        if (id_ := numeric_id(option.attrs.get("value"))) and id_ != "0" and (label := clean_label(option.text())):
            choices.append(Choice(id_, label))
    return choices


def parse_street(page: str) -> dict[str, dict[str, Counter[str]]]:
    """House → household / other consumers → queues ("4.1", or the note when it is not a queue)."""
    houses: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    columns: dict[str, int] = {}
    for row in parse_html(page).find_all("tr")[:MAX_ENTRIES]:
        cells = [c.text() for c in row.children if isinstance(c, Node) and c.tag == "td"]
        if "Будинки" in cells and "Черга" in cells and "Підчерга" in cells:
            columns = {name: cells.index(name) for name in ("Будинки", "Черга", "Підчерга", "Примітка") if name in cells}
            continue
        if not columns or len(cells) <= max(columns.values()):
            continue
        queue, subqueue = cells[columns["Черга"]], cells[columns["Підчерга"]]
        digits = re.fullmatch(r"[0-9]{1,2}", queue) and re.fullmatch(r"[0-9]{1,2}", subqueue)
        group = f"{int(queue)}.{int(subqueue)}" if digits else None
        value = group or ("не вимикається" if "не вимикається" in queue.lower() else clean_label(queue, 40) or "?")
        note = cells[columns["Примітка"]] if "Примітка" in columns else ""
        kind = "household" if HOUSEHOLD in note and "непобутові" not in note else "other"
        for house in cells[columns["Будинки"]].split(","):
            if label := clean_label(house, 30):
                houses[label][kind][value] += 1
    return {house: dict(kinds) for house, kinds in houses.items()}


def resolution(kinds: dict[str, Counter[str]]) -> Resolution:
    """Queues of household consumers if the house has any, else of everyone in it."""
    values = kinds.get("household") or sum(kinds.values(), Counter())
    groups = tuple(v for v, _ in values.most_common() if re.fullmatch(r"[0-9]\.[0-9]", v))
    other = tuple(v for v, _ in values.most_common() if v not in groups)
    return Resolution(groups, other)


class ZhytomyrLookup(AddressLookup):
    hosts = frozenset({"www.ztoe.com.ua"})
    steps = (
        Step("Оберіть РЕМ Житомиробленерго (район)", browse=True, in_label=False),
        Step("Напишіть назву населеного пункту", "Житомир", browse=True),
        Step("Напишіть назву вулиці", "Київська", browse=True),
        Step("Напишіть номер будинку", "49", browse=True, house=True),
    )

    async def _page(self, http: Fetcher, path: Sequence[str]) -> str:
        params = {name: require_id(id_) for name, id_ in zip(_SELECTS, path, strict=False)}
        if len(path) == 3:
            params["all"] = "1"  # every row, not the first ten
        return (await http.get(URL, params=params)).text("cp1251")

    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        page = await self._page(http, path)
        if len(path) == 3:
            return pick((Choice(h, h) for h in parse_street(page)), query)
        return pick(parse_select(page, _SELECTS[len(path)]), query)

    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        if len(path) != len(self.steps):
            raise ValueError("incomplete address")
        kinds = parse_street(await self._page(http, path[:3])).get(path[3])
        if kinds is None:
            raise ValueError("no such house")
        return resolution(kinds)
