"""Check every operator's sources once and write a shields.io endpoint badge per operator.

    uv run python scripts/source_status.py --out status/

Writes ``<operator>.json`` (https://shields.io/badges/endpoint-badge), ``summary.json`` and a
Markdown table; exits with 1 when an operator that should work has no working source. On
GitHub's runners the sites that answer only Ukrainian connections are not queried.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ua_outages import AddressSource, Fetcher, SourceBlocked, now_kyiv
from ua_outages.http import new_session, redact
from ua_outages.registry import LATER, NOT_PUBLISHED, OPERATORS, REGIONS_BY_ID, Operator

#: What a person would type at each step of the address search, for per-address sources.
ADDRESSES = {"ternopil-oe": ["Тернопіль", "Руська", "1"]}
#: Short names for regions with more than one operator.
SHORT = {"dtek-dnem": "ДТЕК", "cek": "ЦЕК"}


@dataclass(frozen=True)
class Status:
    message: str
    color: str
    #: An operator that should work does not: the run fails.
    broken: bool = False


WORKS = Status("працює", "brightgreen")
FALLBACK = Status("працює (резервне джерело)", "yellow")
BROKEN = Status("не працює", "red", broken=True)
UKRAINE_ONLY = Status("перевіряється лише з України", "blue")
BLOCKED = Status("CI заблоковано захистом сайту", "yellow")
NOT_YET = Status("згодом", "lightgrey")
NONE = Status("не публікує", "lightgrey")
UNAVAILABLE = Status("недоступно", "lightgrey")


def label(op: Operator) -> str:
    region = REGIONS_BY_ID[op.region]
    return f"{region.name} · {SHORT[op.id]}" if op.id in SHORT else region.name


async def _address_key(op: Operator, session) -> str:
    lookup = op.lookup
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


async def check_source(op: Operator, source, session, *, on_ci: bool) -> tuple[str, str]:
    """("ok" | "skipped" | "blocked" | "error", detail)."""
    if on_ci and source.ukraine_only:
        return "skipped", "only answers connections from Ukraine"
    try:
        http = Fetcher(session, source.hosts)
        if isinstance(source, AddressSource):
            schedules = await source.fetch_address(http, now_kyiv(), await _address_key(op, session))
        else:
            schedules = await source.fetch(http, now_kyiv())
    except SourceBlocked as err:
        return "blocked", redact(str(err))
    except Exception as err:  # every failure is a finding here
        return "error", redact(f"{type(err).__name__}: {err}")
    for schedule in schedules.values():
        if extra := set(schedule.mentioned_groups()) - set(op.groups):
            return "error", f"unknown groups {sorted(extra)}"
        if schedule.unknown:
            return "error", f"unparsed groups {sorted(schedule.unknown)}"
    days = ", ".join(f"{d:%d.%m}" for d in schedules) or "nothing published"
    return "ok", days


def verdict(op: Operator, results: list[tuple[str, str]]) -> Status:
    if not op.available:
        return NOT_YET if op.note == LATER else NONE if op.note == NOT_PUBLISHED else UNAVAILABLE
    kinds = [kind for kind, _ in results]
    if kinds[0] == "ok":
        return WORKS
    if "ok" in kinds:
        return FALLBACK
    if "error" in kinds:
        return BROKEN
    return UKRAINE_ONLY if "skipped" in kinds else BLOCKED


async def run(out: Path, *, on_ci: bool) -> bool:
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    summary = {}
    healthy = True
    async with new_session() as session:
        for op in OPERATORS:
            results = [await check_source(op, s, session, on_ci=on_ci) for s in op.sources]
            status = verdict(op, results)
            healthy &= not status.broken
            badge = {"schemaVersion": 1, "label": label(op), "message": status.message, "color": status.color}
            (out / f"{op.id}.json").write_text(json.dumps(badge, ensure_ascii=False), encoding="utf-8")
            summary[op.id] = {
                "status": status.message,
                "sources": [{"source": s.name, "result": kind, "detail": detail} for s, (kind, detail) in zip(op.sources, results)],
            }
            details = "; ".join(f"{s.name}: {kind} ({detail})" for s, (kind, detail) in zip(op.sources, results))
            rows.append(f"| {label(op)} | {op.name} | {status.message} | {details or op.note or ''} |")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    table = "\n".join(["| Область | Оператор | Статус | Джерела |", "|---|---|---|---|", *rows])
    print(table)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(path).write_text("### Джерела графіків\n\n" + table + "\n", encoding="utf-8")
    return healthy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("status"))
    args = parser.parse_args()
    healthy = asyncio.run(run(args.out, on_ci=os.environ.get("GITHUB_ACTIONS") == "true"))
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
