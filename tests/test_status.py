"""The source status check (``ua_outages.status``) and the README table that shows it."""

import asyncio
import json
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
    badge_markdown,
    official_cell,
    readme_table,
    source_cell,
    ordered,
    table,
    verdict,
)

README = Path(__file__).parent.parent / "README.md"
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
    sumy = summary["operators"]["sumy-oe"]
    assert sumy["official"] is True and summary["operators"]["crimea"]["official"] is None
    assert sumy["sources"] == [
        {
            "source": "SumyNews",
            "kind": "картинка з сайту",
            "official": True,
            "link": "https://www.soe.com.ua/press-service/news",
            "result": "ok",
            "detail": "20.10",
        }
    ]
    rows = table(statuses).splitlines()
    assert rows[0] == "| Область | Оператор | Стан | Джерело офіційне | Джерело | Відповідь джерел |"
    assert rows[2] == (
        r"| Сумська | Сумиобленерго | працює | ✅ | [картинка з сайту](https://www.soe.com.ua/press-service/news) "
        r"| SumyNews: ok \(20.10\) |"
    )
    assert rows[3].startswith("| АР Крим | — | тимчасово окупована") and rows[3].count("| — |") == 2


def test_what_each_operator_reads():
    """Official: the operator's own site, API or channel; a mirror is not. Fallbacks after «резерв»."""
    assert {op.id for op in ordered() if op.available and not op.official} == {"dtek-krem"}
    assert official_cell(operator("lviv-oe")) == "✅"
    assert official_cell(operator("dtek-kem")) == "✅ · резерв ❌"
    assert official_cell(operator("dtek-dnem")) == "✅ · резерв ✅ ❌"
    assert official_cell(operator("kherson-oe")) == "—"
    assert source_cell(operator("dtek-kem")) == (
        "[API](https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages)"
        " · резерв: [неофіційне дзеркало](https://github.com/Baskerville42/outage-data-ua)"
    )
    assert source_cell(operator("kirovohrad-oe")).startswith("[Telegram-канал](https://t.me/s/SvitloKropyvnytskyiMisto)")
    assert source_cell(operator("ternopil-oe")) == "API"  # per address: no one link
    # Brackets in an address must not end the Markdown link.
    assert "option_key%5B%5D=pw_gpv_image_today" in source_cell(operator("volyn-oe"))
    kinds = {str(s.kind) for op in ordered() for s in op.sources}
    assert kinds == {"API", "сторінка сайту", "картинка з сайту", "Telegram-канал", "неофіційне дзеркало"}


def test_readme_table_is_generated():
    """The README's table is ``readme_table()``: regenerate it rather than edit it."""
    text = README.read_text(encoding="utf-8")
    start = text.index("<!-- sources table: generated by ua_outages.status.readme_table() -->\n") + len(
        "<!-- sources table: generated by ua_outages.status.readme_table() -->\n"
    )
    assert text[start : text.index("\n<!-- end of sources table -->")] == readme_table()


def test_fixed_badges_need_nothing_published():
    """Occupied regions and operators without sources never change: a fixed badge, no fetch."""
    assert badge_markdown(operator("crimea")) == badge_markdown(operator("sevastopol")).replace("sevastopol", "crimea")
    assert badge_markdown(operator("crimea")).startswith("![crimea](https://img.shields.io/badge/-")
    assert badge_markdown(operator("luhansk-leo")).startswith("![luhansk-leo](https://img.shields.io/badge/-")
    assert badge_markdown(operator("lviv-oe")) == (
        "[![lviv-oe](https://raw.githubusercontent.com/fastiuk/ukraine-energy-monitor-status/main/lviv-oe.svg)]"
        "(https://github.com/fastiuk/ukraine-energy-monitor-status#readme)"
    )
