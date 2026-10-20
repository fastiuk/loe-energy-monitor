"""The source status check (``ua_outages.status``) and the README table that shows it."""

import asyncio
import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from ua_outages import FetchError, Provider, SourceBlocked, operator
from ua_outages.models import KYIV, DaySchedule
from ua_outages.registry import Operator
from ua_outages.status import (
    BLOCKED,
    BROKEN,
    FALLBACK,
    NONE,
    OCCUPIED,
    UKRAINE_ONLY,
    WORKS,
    OperatorStatus,
    SourceResult,
    check_all,
    check_source,
    files,
    official_api_mark,
    ordered,
    table,
    verdict,
)

README = Path(__file__).parent.parent / "README.md"
BADGES = "https%3A%2F%2Fraw.githubusercontent.com%2Ffastiuk%2Fukraine-energy-monitor-status%2Fmain%2F"
DAY = date(2026, 10, 20)


def ok(name="a"):
    return SourceResult(name, "ok", "20.10")


def failed(name="a", kind="error"):
    return SourceResult(name, kind, "HTTP 403")


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([ok(), failed("b")], WORKS),
        ([failed(), ok("b")], FALLBACK),
        ([failed(), failed("b", "blocked")], BROKEN),
        ([failed("a", "blocked")], BLOCKED),
        ([failed("a", "skipped")], UKRAINE_ONLY),
    ],
)
def test_verdict(results, expected):
    assert verdict(operator("dtek-kem"), results) == expected


def test_operators_without_sources_say_why():
    assert verdict(operator("crimea"), []) == OCCUPIED
    assert verdict(operator("luhansk-leo"), []) == NONE


class Source(Provider):
    hosts = frozenset({"example.invalid"})

    def __init__(self, answer, *, ukraine_only=False):
        self.answer, self.ukraine_only = answer, ukraine_only

    async def fetch(self, http, now):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def check(source, op=None):
    op = op or Operator("test", "Test", "kyiv", "", sources=(source,))
    return asyncio.run(check_source(op, source, session=None, abroad=True))


def test_one_source_checked():
    schedule = DaySchedule(DAY, datetime(2026, 10, 20, 9, tzinfo=KYIV), {"1.1": ()}, {})
    assert check(Source({DAY: schedule})) == SourceResult("Source", "ok", "20.10")
    assert check(Source({})).detail == "nothing published"
    # Errors are published: query strings (an address in a per-address source) are cut.
    failure = check(Source(FetchError("GET https://example.invalid/x?street=Руська&house=1: HTTP 500")))
    assert failure.result == "error" and "Руська" not in failure.detail and "?…" in failure.detail
    assert check(Source(SourceBlocked("captcha"))).result == "blocked"
    assert check(Source({}, ukraine_only=True)).result == "skipped"
    unknown = DaySchedule(DAY, datetime(2026, 10, 20, 9, tzinfo=KYIV), {}, {"1.1": "?"})
    assert check(Source({DAY: unknown})) == SourceResult("Source", "error", "unparsed groups ['1.1']")


def test_check_all_covers_every_operator_once(monkeypatch):
    async def fake(op, source, session, *, abroad=False):
        return SourceResult(source.name, "ok", "nothing published")

    monkeypatch.setattr("ua_outages.status.check_source", fake)
    statuses = asyncio.run(check_all(None))
    assert [s.operator.id for s in statuses] == [op.id for op in ordered()]
    by_id = {s.operator.id: s for s in statuses}
    assert by_id["lviv-oe"].status == WORKS and by_id["crimea"].status == OCCUPIED


def test_published_files():
    statuses = [OperatorStatus(operator("sumy-oe"), WORKS, (ok("SumyNews"),)), OperatorStatus(operator("crimea"), OCCUPIED, ())]
    out = files(statuses, datetime(2026, 10, 20, 12, tzinfo=KYIV))
    assert json.loads(out["sumy-oe.json"]) == {"schemaVersion": 1, "label": "Сумська", "message": "працює", "color": "brightgreen"}
    summary = json.loads(out["summary.json"])
    assert summary["checked_at"] == "2026-10-20T12:00:00+03:00"
    assert summary["operators"]["sumy-oe"]["official_api"] is False and summary["operators"]["crimea"]["official_api"] is None
    rows = table(statuses).splitlines()
    assert rows[0] == "| Область | Оператор | Статус | API з офіційного ресурсу | Джерела |"
    assert rows[2] == r"| Сумська | Сумиобленерго | працює | ❌ | SumyNews: ok \(20.10\) |"
    assert rows[3].startswith("| АР Крим | Автономна Республіка Крим | тимчасово окупована") and "| — |" in rows[3]


def test_official_api_marks():
    """✅ only where the schedule comes as data from the operator's own API."""
    official = {op.id for op in ordered() if op.official_api}
    assert official == {"dtek-kem", "dtek-dnem", "cek", "prykarpattia-oe", "lviv-oe", "mykolaiv-oe", "ternopil-oe", "cherkasy-oe"}
    assert official_api_mark(operator("lviv-oe")) == "✅"
    assert official_api_mark(operator("kirovohrad-oe")) == "❌"  # Telegram
    assert official_api_mark(operator("dtek-krem")) == "❌"  # a mirror
    assert official_api_mark(operator("kherson-oe")) == "—"


def test_readme_table_matches_the_registry():
    """Every operator in order, its badge from the status repo, the API mark and 🔎 right."""
    rows = [line for line in README.read_text(encoding="utf-8").splitlines() if "img.shields.io/endpoint" in line]
    assert len(rows) == len(ordered())
    for op, row in zip(ordered(), rows, strict=True):
        cells = [c.strip() for c in row.strip("|").split("|")]
        badge = re.search(r"\[!\[([a-z-]+)\]\(([^)]+)\)\]", row)
        assert badge and badge[1] == op.id, row
        assert f"url={BADGES}{op.id}.json" in badge[2], row
        assert cells[3] == official_api_mark(op), op.id
        assert cells[4] == ("🔎" if op.lookup else ""), op.id
