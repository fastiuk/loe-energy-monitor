"""DTEK schedules: the ``DisconSchedule.fact`` grid on dtek-*.com.ua and its outage-data-ua mirror.

::

    DisconSchedule.fact = {"data": {"1792270800": {"GPV1.1": {"1": "yes", ..., "20": "first", "24": "no"}}},
                           "update": "18.10.2026 14:59", "today": 1792270800}

Day keys are the Unix time of Kyiv midnight; hour keys 1–24 each cover one hour. ``first`` /
``second`` mean no power in that half of the hour, ``m*`` / ``maybe`` that there may be none.
With nothing published ``data`` is an empty list.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..http import FetchError, Fetcher
from ..models import KYIV, DaySchedule, Outage, Slot, outages_from_slots
from .base import Provider, SourceBlocked, fail

#: Hour value → its two half-hour cells.
HOUR_STATES = {
    "yes": (Slot.ON, Slot.ON),
    "no": (Slot.OFF, Slot.OFF),
    "first": (Slot.OFF, Slot.ON),
    "second": (Slot.ON, Slot.OFF),
    "maybe": (Slot.MAYBE, Slot.MAYBE),
    "mfirst": (Slot.MAYBE, Slot.ON),
    "msecond": (Slot.ON, Slot.MAYBE),
}
MAX_DAYS = 7
MAX_GROUPS = 300
_GPV_RE = re.compile(r"GPV(\d{1,4})\.(\d{1,2})")
_UPDATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})")

GroupMap = Callable[[str], str | None]


def plain_groups(key: str) -> str | None:
    """"GPV4.2" → "4.2"; skips ЦЕК's GPV1001.1… that DnEM pages carry too."""
    if (m := _GPV_RE.fullmatch(key)) and int(m[1]) < 1000:
        return f"{m[1]}.{m[2]}"
    return None


def cek_groups(key: str) -> str | None:
    """ЦЕК groups on DnEM pages: "GPV1003.2" → "3.2"."""
    if (m := _GPV_RE.fullmatch(key)) and 1001 <= int(m[1]) <= 1099:
        return f"{int(m[1]) - 1000}.{m[2]}"
    return None


def _hours(day: date, hours: Any) -> tuple[Outage, ...] | None:
    if not isinstance(hours, dict):
        return None
    slots: list[Slot] = []
    for hour in range(1, 25):
        states = HOUR_STATES.get(hours.get(str(hour)))
        if states is None:
            return None
        slots += states
    return outages_from_slots(day, slots)


def parse_fact(fact: Any, groups: GroupMap = plain_groups) -> dict[date, DaySchedule]:
    if not isinstance(fact, dict) or "data" not in fact:
        fail("DTEK: fact has no data")
    data = fact["data"]
    if data == []:
        return {}
    if not isinstance(data, dict) or len(data) > MAX_DAYS:
        fail("DTEK: fact data is not a day map")
    updated = None
    if isinstance(fact.get("update"), str) and (m := _UPDATE_RE.fullmatch(fact["update"])):
        try:
            updated = datetime(int(m[3]), int(m[2]), int(m[1]), int(m[4]), int(m[5]), tzinfo=KYIV)
        except ValueError:
            pass

    schedules = {}
    for stamp, day_groups in data.items():
        try:
            day = datetime.fromtimestamp(int(stamp), UTC).astimezone(KYIV).date()
        except (ValueError, OverflowError, OSError):
            fail(f"DTEK: bad day key {str(stamp)[:20]!r}")
        if not isinstance(day_groups, dict) or len(day_groups) > MAX_GROUPS:
            fail("DTEK: day is not a group map")
        parsed: dict[str, tuple[Outage, ...]] = {}
        unknown: dict[str, str] = {}
        for key, hours in day_groups.items():
            if (group := groups(key)) is None:
                continue
            outages = _hours(day, hours)
            if outages is None:
                unknown[group] = "Нерозпізнаний графік"
            else:
                parsed[group] = outages
        if parsed or unknown:
            schedules[day] = DaySchedule(day, updated, parsed, unknown)
    return dict(sorted(schedules.items()))


def extract_fact(page: str) -> Any:
    """The JSON assigned to ``DisconSchedule.fact`` in a shutdowns page."""
    marker = "DisconSchedule.fact"
    start = page.find(marker)
    if start < 0:
        if "Incapsula" in page or "_Incapsula_Resource" in page:
            raise SourceBlocked("DTEK: the page is blocked by Imperva")
        fail("DTEK: no DisconSchedule.fact in the page")
    brace = page.find("{", start, start + 100)
    if brace < 0:
        fail("DTEK: DisconSchedule.fact is not an object")
    try:
        fact, _ = json.JSONDecoder().raw_decode(page, brace)
    except ValueError as err:
        fail(f"DTEK: DisconSchedule.fact is not JSON ({err})")
    return fact


class DtekPage(Provider):
    """A dtek-{network}.com.ua shutdowns page that is not behind a hard Imperva block."""

    #: The page itself checks for updates every 5 minutes; robots.txt asks bots to stay away,
    #: so never poll more often than a visitor's browser would.
    min_interval = 300

    def __init__(self, network: str, groups: GroupMap = plain_groups) -> None:
        if not re.fullmatch(r"[a-z]{2,5}", network):
            raise ValueError(f"Bad DTEK network {network!r}")
        self.network = network
        self.groups = groups
        self.url = f"https://www.dtek-{network}.com.ua/ua/shutdowns"

    @property
    def hosts(self) -> frozenset[str]:  # type: ignore[override]
        return frozenset({f"www.dtek-{self.network}.com.ua"})

    @property
    def name(self) -> str:
        return f"DTEK {self.network.upper()}"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(self.url, headers={"Accept": "text/html", "Accept-Language": "uk"})
        return await asyncio.to_thread(lambda: parse_fact(extract_fact(resp.text()), self.groups))


MIRROR_URL = "https://raw.githubusercontent.com/Baskerville42/outage-data-ua/main/data/{region}.json"
#: The mirror refreshes every few minutes; hours without a refresh mean it stopped working.
MIRROR_MAX_AGE = timedelta(hours=3)


def parse_mirror(payload: Any, now: datetime, groups: GroupMap = plain_groups) -> dict[date, DaySchedule]:
    if not isinstance(payload, dict):
        fail("outage-data-ua: expected an object")
    status = payload.get("lastUpdateStatus")
    if isinstance(status, dict) and status.get("ok") is False:
        raise FetchError(f"outage-data-ua: the mirror's last update failed (code {status.get('code')!r})")
    try:
        refreshed = datetime.fromisoformat(str(payload.get("lastUpdated")).replace("Z", "+00:00"))
    except ValueError:
        fail("outage-data-ua: no lastUpdated")
    if refreshed.tzinfo is None or now - refreshed > MIRROR_MAX_AGE:
        raise FetchError(f"outage-data-ua: the mirror was last refreshed at {refreshed.isoformat()}")
    return parse_fact(payload.get("fact"), groups)


class OutageDataUa(Provider):
    """github.com/Baskerville42/outage-data-ua (MIT): DTEK data for regions behind Imperva."""

    hosts = frozenset({"raw.githubusercontent.com"})
    #: raw.githubusercontent.com caches files for 5 minutes.
    min_interval = 300

    def __init__(self, region: str, groups: GroupMap = plain_groups) -> None:
        if not re.fullmatch(r"[a-z-]{2,20}", region):
            raise ValueError(f"Bad outage-data-ua region {region!r}")
        self.region = region
        self.groups = groups

    @property
    def name(self) -> str:
        return f"outage-data-ua {self.region}"

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        resp = await http.get(MIRROR_URL.format(region=self.region))
        return parse_mirror(resp.json(), now, self.groups)
