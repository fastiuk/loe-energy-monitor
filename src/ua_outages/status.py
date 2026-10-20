"""Whether each operator's schedules can be fetched right now: one check of every source.

Used by ``scripts/source_status.py`` in CI and by the Telegram bot, which checks from Ukraine
every hour and publishes the result (fastiuk/ukraine-energy-monitor-status). Per operator: a
shields.io endpoint badge (https://shields.io/badges/endpoint-badge), an entry of
``summary.json`` and a row of a Markdown table.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .http import Fetcher, redact
from .models import now_kyiv
from .providers.base import AddressSource, Provider, SourceBlocked
from .registry import LATER, NOT_PUBLISHED, OPERATORS_BY_ID, REGIONS, REGIONS_BY_ID, Operator

#: What a person would type at each step of the address search, for per-address sources: the
#: operators' own offices.
ADDRESSES = {"ternopil-oe": ["Тернопіль", "Руська", "1"], "vinnytsia-oe": ["Вінниця", "Магістратська", "2"]}
#: Short names for regions with more than one operator.
SHORT = {"dtek-dnem": "ДТЕК", "cek": "ЦЕК"}


@dataclass(frozen=True)
class Status:
    message: str
    color: str
    #: An operator that should work does not.
    broken: bool = False


WORKS = Status("працює", "brightgreen")
FALLBACK = Status("працює (резервне джерело)", "yellow")
BROKEN = Status("не працює", "red", broken=True)
UKRAINE_ONLY = Status("перевіряється лише з України", "blue")
BLOCKED = Status("заблоковано захистом сайту", "yellow")
NOT_YET = Status("згодом", "lightgrey")
NONE = Status("не публікує", "lightgrey")
UNAVAILABLE = Status("недоступно", "lightgrey")
OCCUPIED = Status("тимчасово окупована — буде після повернення додому", "0057b7")


@dataclass(frozen=True)
class SourceResult:
    source: str
    #: "ok", "skipped" (not queried from abroad), "blocked" or "error".
    result: str
    detail: str


@dataclass(frozen=True)
class OperatorStatus:
    operator: Operator
    status: Status
    sources: tuple[SourceResult, ...]

    def badge(self) -> dict[str, Any]:
        return {"schemaVersion": 1, "label": label(self.operator), "message": self.status.message, "color": self.status.color}

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status.message,
            "official_api": self.operator.official_api if self.operator.available else None,
            "sources": [{"source": s.source, "result": s.result, "detail": s.detail} for s in self.sources],
        }

    def row(self) -> str:
        op = self.operator
        details = "; ".join(f"{s.source}: {s.result} ({s.detail})" for s in self.sources) or op.note or ""
        return f"| {label(op)} | {cell(op.name)} | {self.status.message} | {official_api_mark(op)} | {cell(details, 1000)} |"


def ordered() -> list[Operator]:
    """Operators in the order of regions (Kyiv, Crimea, oblasts, Sevastopol)."""
    return [OPERATORS_BY_ID[o] for region in REGIONS for o in region.operators]


def label(op: Operator) -> str:
    region = REGIONS_BY_ID[op.region]
    return f"{region.name} · {SHORT[op.id]}" if op.id in SHORT else region.name


def official_api_mark(op: Operator) -> str:
    """✅ the schedule comes from the operator's own API, ❌ from a page, a picture, a channel or a
    mirror, — no schedules at all."""
    return "—" if not op.available else "✅" if op.official_api else "❌"


#: Characters with a meaning in Markdown, and what starts a bare URL GitHub turns into a
#: link: the ":" of "://", the "." after "www".
_MARKDOWN_RE = re.compile(r"([\\`*_{}\[\]()<>#+!|~&]|:(?=//)|(?<=www)\.)", re.IGNORECASE)


def cell(text: str, limit: int = 300) -> str:
    """Text from a source, shown as-is in a Markdown table (no links, no extra columns).

    Each of those characters gets a backslash, which the renderer drops: "https\\://x" and
    "www\\.x" show as "https://x" and "www.x", but not as links.
    """
    text = " ".join(text.split())[:limit]
    return _MARKDOWN_RE.sub(r"\\\1", text)


async def _address_key(op: Operator, session: Any) -> str:
    lookup = op.lookup
    assert lookup is not None
    http = Fetcher(session, lookup.hosts, max_bytes=lookup.max_bytes)
    path: list[str] = []
    for query in ADDRESSES[op.id]:
        choices = await lookup.options(http, path, query)
        if not choices:
            raise LookupError(f"nothing found for {query!r}")
        path.append(choices[0].id)
        await asyncio.sleep(lookup.request_gap)
    resolution = await lookup.resolve(http, path)
    if not resolution.address:
        raise LookupError("the lookup gave no address key")
    return resolution.address


async def check_source(op: Operator, source: Provider, session: Any, *, abroad: bool = False) -> SourceResult:
    """One fetch from one source; ``abroad`` skips the sites that answer only Ukrainian connections."""
    if abroad and source.ukraine_only:
        return SourceResult(source.name, "skipped", "only answers connections from Ukraine")
    try:
        http = Fetcher(session, source.hosts)
        if isinstance(source, AddressSource):
            schedules = await source.fetch_address(http, now_kyiv(), await _address_key(op, session))
        else:
            schedules = await source.fetch(http, now_kyiv())
    except SourceBlocked as err:
        return SourceResult(source.name, "blocked", redact(str(err)))
    except Exception as err:  # every failure is a finding here
        return SourceResult(source.name, "error", redact(f"{type(err).__name__}: {err}"))
    for schedule in schedules.values():
        if extra := set(schedule.mentioned_groups()) - set(op.groups):
            return SourceResult(source.name, "error", f"unknown groups {sorted(extra)}")
        if schedule.unknown and not source.hours_on_picture:
            return SourceResult(source.name, "error", f"unparsed groups {sorted(schedule.unknown)}")
    days = ", ".join(f"{d:%d.%m}" for d in schedules) or "nothing published"
    return SourceResult(source.name, "ok", days)


def verdict(op: Operator, results: list[SourceResult] | tuple[SourceResult, ...]) -> Status:
    if REGIONS_BY_ID[op.region].occupied:
        return OCCUPIED
    if not op.available:
        return NOT_YET if op.note == LATER else NONE if op.note == NOT_PUBLISHED else UNAVAILABLE
    kinds = [r.result for r in results]
    if kinds[0] == "ok":
        return WORKS
    if "ok" in kinds:
        return FALLBACK
    if "error" in kinds:
        return BROKEN
    return UKRAINE_ONLY if "skipped" in kinds else BLOCKED


async def check_all(session: Any, *, abroad: bool = False) -> list[OperatorStatus]:
    """Every operator, one source after another (one request at a time per site)."""
    statuses = []
    for op in ordered():
        results = tuple([await check_source(op, s, session, abroad=abroad) for s in op.sources])
        statuses.append(OperatorStatus(op, verdict(op, results), results))
    return statuses


def table(statuses: list[OperatorStatus]) -> str:
    header = "| Область | Оператор | Статус | API з офіційного ресурсу | Джерела |"
    return "\n".join([header, "|---|---|---|---|---|", *(s.row() for s in statuses)])


def files(statuses: list[OperatorStatus], checked_at: datetime) -> dict[str, str]:
    """``<operator>.json`` badges and ``summary.json``, by file name."""
    out = {f"{s.operator.id}.json": json.dumps(s.badge(), ensure_ascii=False) for s in statuses}
    summary = {"checked_at": checked_at.isoformat(timespec="seconds"), "operators": {s.operator.id: s.summary() for s in statuses}}
    out["summary.json"] = json.dumps(summary, ensure_ascii=False, indent=1)
    return out
