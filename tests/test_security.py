"""Sources are untrusted: hostile or broken responses must stay harmless and cheap."""

import asyncio
import ssl
import struct
import time
import zipfile
from datetime import date

import aiohttp
import pytest
from aiohttp import web
from conftest import MAIN_NS, fixture_json, load_script, workbook
from multidict import CIMultiDict

from ua_outages.http import FetchError, Fetcher, Response, Throttle, forbidden_host, ssl_context
from ua_outages.providers import kharkiv, mykolaiv, ternopil, yasno
from ua_outages.providers._html import MAX_NODES, TooManyNodes, parse_html
from ua_outages.providers._text import MAX_TEXT, find_day, html_lines, parse_group_text, parse_intervals, plain_text
from ua_outages.providers._xlsx import MAX_BYTES, MAX_CELL, MAX_COLUMNS, MAX_PART, MAX_PARTS, MAX_ROWS, read_sheet
from ua_outages.providers.base import SourceFormatError
from ua_outages.providers.loe import _image_url

DAY = date(2026, 10, 17)
KHARKIV_TITLE = "<title>У Харківській області 17 жовтня діятимуть графіки погодинних відключень</title>"


def _fast(fn, *args, limit: float = 2.0):
    start = time.perf_counter()
    result = fn(*args)
    assert time.perf_counter() - start < limit
    return result


@pytest.mark.parametrize(
    "markup",
    [
        "<div>" * 200_000,  # deep nesting
        "<div>" * 300 + "</span>" * 200_000,  # stray end tags far below the open ones
        "<a>" + "<div>" * 300 + "</a>" * 200_000,  # end tags that match an open element once
        "<" * 500_000,
        "<p" * 250_000,
        "<script>" + "<div>" * 100_000,
    ],
)
def test_html_parser_is_linear_and_flat(markup):
    try:
        root = _fast(parse_html, markup)
    except TooManyNodes:  # also fine: refused in linear time (_fast would have failed otherwise)
        return
    _fast(root.text)
    _fast(root.find_all, "td")


def test_html_parser_refuses_huge_trees_cheaply():
    with pytest.raises(TooManyNodes):
        _fast(parse_html, "<br>" * (MAX_NODES + 1))
    assert sum(1 for _ in parse_html("<br>" * MAX_NODES).iter()) == MAX_NODES + 1  # plus the root
    root = parse_html("<td>1</td>" * 1000 + "<td class=x>2</td>")
    assert root.find("td", cls="x").text() == "2" and root.find("th") is None


@pytest.mark.parametrize(
    "text",
    [
        "1.1 " + "10:00-" * 50_000,
        "1.1 1.2 " * 50_000,
        "з 10:00 до " * 30_000,
        "1." * 200_000,
        "<" + " " * 200_000,  # the block-tag pattern once backtracked over runs of spaces
        "< /" + " " * 200_000 + "p",
        "<p" + " " * 200_000,
    ],
)
def test_text_parsers_are_linear(text):
    _fast(parse_group_text, DAY, text)
    _fast(html_lines, text)
    _fast(parse_intervals, DAY, text[:MAX_TEXT])


def test_text_with_a_limit_reads_only_the_start():
    root = parse_html("<div>" + "<p>слово</p>" * 40_000 + "</div>")
    text = _fast(root.text, 100)
    assert len(text) <= 100 and text.startswith("слово слово")
    assert parse_html("<p>1.1  08:00-10:00</p><p>1.2</p>").text(14) == "1.1 08:00-10"


@pytest.mark.parametrize(
    "markup",
    ["<p>&#" + "1" * 5000 + ";</p>", '<a title="&#' + "1" * 5000 + '">x</a>', "<p>&#" + "1" * 5000],
    ids=["text", "attribute", "unclosed"],
)
def test_overlong_character_references_are_a_format_error(markup):
    """html.unescape raises ValueError for a decimal reference of over 4300 digits."""
    for parse in (parse_html, html_lines, plain_text):
        with pytest.raises(SourceFormatError):
            _fast(parse, markup)
    with pytest.raises(SourceFormatError):
        kharkiv.parse_feed(f"<rss><item>{KHARKIV_TITLE}<description>{markup}</description></item></rss>", DAY)


def test_look_alike_letters_are_not_months():
    # IGNORECASE matches U+1C82 (wide o) for "о"; .lower() keeps it, so it names no month.
    assert find_day("Графік на 17 ж\u1c82втня", near=DAY) is None
    assert find_day("Графік на 17 жовтня", near=DAY) == DAY


@pytest.mark.parametrize("moment", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-05:00"])
def test_times_out_of_range_in_kyiv_are_not_crashes(moment):
    """Valid ISO times that fall before year 1 or after 9999 in Kyiv time: astimezone overflows."""
    assert ternopil._datetime(moment) is None and yasno._datetime(moment) is None
    with pytest.raises(SourceFormatError):
        mykolaiv.parse(fixture_json("mykolaiv_queues.json"), fixture_json("mykolaiv_series.json"), [{"from": moment}])


def test_rss_dates_out_of_range_in_kyiv_are_not_crashes():
    item = f"{KHARKIV_TITLE}<pubDate>Fri, 31 Dec 9999 23:59:59 -0500</pubDate><description>1.1 10:00-12:00</description>"
    assert kharkiv.parse_feed(f"<rss><item>{item}</item></rss>", DAY)[DAY].updated_at is None


def test_invalid_times_are_rejected():
    assert parse_intervals(DAY, "25:00-26:00") is None
    assert parse_intervals(DAY, "10:61-11:00") is None
    assert parse_intervals(DAY, "нічого") is None


def test_script_content_is_not_text():
    root = parse_html("<p>1.1 10:00-12:00</p><script>document.write('1.2 00:00-24:00')</script>")
    assert "1.2" not in root.text()


def test_loe_image_url_stays_on_the_api_host():
    assert _image_url("/media/a.png") == "https://api.loe.lviv.ua/media/a.png"
    for hostile in ("//evil.example/a.png", "@evil.example/a.png", "https://evil.example/a.png", "/a b.png", 5):
        assert _image_url(hostile) is None


async def _serve(handler, request):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            fetcher = Fetcher(session, ["127.0.0.1"], schemes=["http"], ports=[port], max_bytes=1024 * 1024)
            return await request(fetcher, f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()


def test_fetcher_refuses_hosts_it_was_not_given():
    async def run():
        async with aiohttp.ClientSession() as session:
            fetcher = Fetcher(session, ["api.loe.lviv.ua"])
            for url in (
                "https://evil.example/",
                "http://api.loe.lviv.ua/",
                "file:///etc/passwd",
                "https://api.loe.lviv.ua:8443/",  # a port the operator never uses
            ):
                with pytest.raises(FetchError, match="not allowed"):
                    await fetcher.get(url)

    asyncio.run(run())


def test_fetcher_does_not_follow_redirects_to_other_hosts():
    async def handler(request):
        raise web.HTTPFound("http://169.254.169.254/latest/meta-data/")

    with pytest.raises(FetchError, match="not allowed"):
        asyncio.run(_serve(handler, lambda f, base: f.get(base + "/x")))


def test_fetcher_follows_redirects_on_the_same_host():
    async def handler(request):
        if request.path == "/old":
            raise web.HTTPMovedPermanently("/new")
        return web.Response(text="ok")

    resp = asyncio.run(_serve(handler, lambda f, base: f.get(base + "/old")))
    assert resp.text() == "ok" and resp.url.endswith("/new")


def test_fetcher_rejects_oversized_bodies():
    async def handler(request):
        response = web.StreamResponse()
        await response.prepare(request)
        for _ in range(3):
            await response.write(b" " * (512 * 1024))
        return response

    with pytest.raises(FetchError, match="over"):
        asyncio.run(_serve(handler, lambda f, base: f.get(base + "/")))


def test_fetcher_reports_http_errors_and_bad_json():
    async def handler(request):
        if request.path == "/500":
            return web.Response(status=500)
        return web.Response(text="<html>challenge</html>")

    with pytest.raises(FetchError, match="HTTP 500"):
        asyncio.run(_serve(handler, lambda f, base: f.get(base + "/500")))
    resp = asyncio.run(_serve(handler, lambda f, base: f.get(base + "/")))
    with pytest.raises(FetchError, match="not JSON"):
        resp.json()


def test_deeply_nested_json_is_a_fetch_error():
    deep = Response("https://x.invalid/", 200, CIMultiDict(), b"[" * 1_000_000, "utf-8")
    with pytest.raises(FetchError):
        deep.json()


def test_fetcher_never_contacts_the_aggressors_domains():
    async def run():
        async with aiohttp.ClientSession() as session:
            hosts = [
                "krymenergo.ru",
                "example.su",
                "xn--80aswg.xn--p1ai",
                "сайт.рф",
                "RU",
                "evil.RU.",
                "сайт.рус",
                "x.xn--p1acf",
                "x.moscow",
                "x.москва",
                "x.tatar",
                "x.дети",
            ]
            fetcher = Fetcher(session, hosts)
            for host in hosts:
                with pytest.raises(FetchError, match="not allowed"):
                    await fetcher.get(f"https://{host}/")

    asyncio.run(run())
    assert not forbidden_host("api.loe.lviv.ua") and not forbidden_host("rubizhne.example.com")


@pytest.mark.parametrize(
    "feed",
    [
        "<item>" * 100_000,  # never closed: one regex retried from every <item>
        "<item><title>" * 50_000 + "</item>",
        "<item>" + "<description>" * 50_000 + "</item>",
    ],
    ids=["unclosed-items", "unclosed-titles", "unclosed-descriptions"],
)
def test_kharkiv_feed_is_linear(feed):
    assert _fast(kharkiv.parse_feed, "<rss>" + feed, DAY) == {}


@pytest.mark.parametrize("charset", ["punycode", "idna", "utf-7", "rot13", "no-such-charset", "zlib"])
def test_responses_decode_only_with_known_charsets(charset):
    """The server's Content-Type picked the decoder: punycode is quadratic, others raised."""
    body = "Графік".encode() + b"-" * 200_000
    resp = Response("https://x.invalid/", 200, CIMultiDict(), body, charset)
    assert _fast(resp.text).startswith("Графік")
    assert Response("https://x.invalid/", 200, CIMultiDict(), "Графік".encode("cp1251"), "windows-1251").text() == "Графік"


def test_throttle_frees_its_lock_when_a_waiter_is_cancelled():
    async def run():
        throttle = Throttle(10)
        async with throttle:
            pass
        waiter = asyncio.create_task(throttle.__aenter__())
        await asyncio.sleep(0.05)  # holding the lock, sleeping out the gap
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not throttle._lock.locked()

    asyncio.run(run())


def test_bundled_intermediates_are_not_trust_anchors():
    assert not ssl_context().verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_fetcher_refuses_other_ports():
    async def run():
        async with aiohttp.ClientSession() as session:
            fetcher = Fetcher(session, ["api.loe.lviv.ua"])
            assert fetcher.allowed("https://api.loe.lviv.ua/a") and fetcher.allowed("https://api.loe.lviv.ua:443/a")
            for url in ("https://api.loe.lviv.ua:22/", "https://api.loe.lviv.ua:6379/"):
                assert not fetcher.allowed(url)

    asyncio.run(run())


def test_status_summary_cells_stay_plain_text():
    cell = load_script("source_status").cell
    assert cell("HTTP 500 | ![x](https://evil.example/t.png)\n## big") == (
        r"HTTP 500 \| \!\[x\]\(https\://evil.example/t.png\) \#\# big"
    )
    # Bare URLs, which GitHub links without any markup (a hostile option value in an error).
    assert cell("bad value HTTPS://evil.example/x or www.evil.example, WWW.x; 10:00 at 1.1") == (
        r"bad value HTTPS\://evil.example/x or www\.evil.example, WWW\.x; 10:00 at 1.1"
    )


def _sheet(rows: str, prolog: str = "") -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>{prolog}<worksheet xmlns="{MAIN_NS}"><sheetData>{rows}</sheetData></worksheet>'


def _declare_size(data: bytes, name: str, size: int) -> bytes:
    """The workbook with ``name`` claiming ``size`` bytes inflated in the zip's directory."""
    data, start = bytearray(data), 0
    while (start := data.find(b"PK\x01\x02", start)) >= 0:
        length = struct.unpack_from("<H", data, start + 28)[0]
        if data[start + 46 : start + 46 + length] == name.encode():
            struct.pack_into("<I", data, start + 24, size)
        start += 4
    return bytes(data)


def test_xlsx_reader_reads_text_cells_within_bounds():
    shared = (
        f'<sst xmlns="{MAIN_NS}"><si><t>Деражня</t></si>'
        "<si><r><t>вул. </t></r><r><t>Миру</t></r><rPh><t>фонетика</t></rPh></si></sst>"
    )
    row = (
        '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c><c><v>12</v></c>'  # no references: in order
        f'<c r="E1" t="inlineStr"><is><t>{"ж" * (MAX_CELL + 10)}</t></is></c>'
        '<c r="XFD1" t="inlineStr"><is><t>far right</t></is></c><c r="F1" t="str"><f>A1</f><v>formula</v></c></row>'
        '<row r="9"><c r="B9"/></row>'
    )
    rows = read_sheet(workbook(sheet=_sheet(row), parts={"xl/sharedStrings.xml": shared}), "test")
    assert rows[0][:4] == ["Деражня", "вул. Миру", "12", ""] and len(rows[0][4]) == MAX_CELL
    assert rows[0][5] == "formula" and len(rows[0]) <= MAX_COLUMNS and rows[1] == ["", ""]


def _laughs() -> str:
    entities = '<!ENTITY a "ha">' + "".join(f'<!ENTITY e{i} "{f"&e{i - 1};" * 10 if i else "&a;" * 10}">' for i in range(10))
    return _sheet('<row><c t="inlineStr"><is><t>&e9;</t></is></c></row>', f"<!DOCTYPE x [{entities}]>")


@pytest.mark.parametrize(
    "data",
    [
        b"not a zip",
        b"PK" + b"x" * (MAX_BYTES + 1),
        workbook(sheet=_laughs()),  # billion laughs
        workbook(sheet=_sheet("<row><c t='inlineStr'><is><t>&e;</t></is></c></row>", '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>')),
        workbook(sheet=_laughs().replace("UTF-8", "UTF-16").encode("utf-16")),  # a DOCTYPE no byte search would see
        workbook(sheet=_sheet("<row><c>")),  # not well-formed
        workbook(sheet=_sheet('<row><c t="s"><v>5</v></c></row>')),  # no shared strings to point at
        workbook(sheet=_sheet('<row><c r="../A1"><v>1</v></c></row>')),
        workbook(sheet=_sheet("<row/>" * (MAX_ROWS + 1))),
        workbook(["x"], parts={"xl/worksheets/sheet1.xml": None}),
        workbook(["x"], parts={"xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="../../../etc/passwd"/></Relationships>'}),
        workbook(["x"], parts={f"junk/{n}": "" for n in range(MAX_PARTS)}),
        workbook(["x"], compression=zipfile.ZIP_BZIP2),  # inflates a whole block at once
        workbook(sheet=_sheet(" " * (MAX_PART + 1))),  # a zip bomb: refused before inflating
        _declare_size(workbook(sheet=_sheet(" " * (MAX_PART + 1))), "xl/worksheets/sheet1.xml", 100),  # lying about it
    ],
    ids=[
        "not-zip", "too-big", "laughs", "external", "utf16", "broken", "shared", "ref", "rows", "no-sheet",
        "sheet-path", "parts", "bzip2", "bomb", "lying-size",
    ],
)  # fmt: skip
def test_xlsx_reader_refuses_hostile_workbooks(data):
    with pytest.raises(SourceFormatError):
        _fast(read_sheet, data, "test")

