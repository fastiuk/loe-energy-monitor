"""Sources are untrusted: hostile or broken responses must stay harmless and cheap."""

import asyncio
import time
from datetime import date

import aiohttp
import pytest
from aiohttp import web

from ua_outages.http import FetchError, Fetcher
from ua_outages.providers._html import parse_html
from ua_outages.providers._text import MAX_TEXT, html_lines, parse_group_text, parse_intervals
from ua_outages.providers.loe import _image_url

DAY = date(2026, 10, 17)


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
    root = _fast(parse_html, markup)
    _fast(root.text)
    _fast(root.find_all, "td")


@pytest.mark.parametrize(
    "text",
    [
        "1.1 " + "10:00-" * 50_000,
        "1.1 1.2 " * 50_000,
        "з 10:00 до " * 30_000,
        "1." * 200_000,
    ],
)
def test_text_parsers_are_linear(text):
    _fast(parse_group_text, DAY, text)
    _fast(html_lines, text)
    _fast(parse_intervals, DAY, text[:MAX_TEXT])


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
            fetcher = Fetcher(session, ["127.0.0.1"], schemes=["http"], max_bytes=1024 * 1024)
            return await request(fetcher, f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()


def test_fetcher_refuses_hosts_it_was_not_given():
    async def run():
        async with aiohttp.ClientSession() as session:
            fetcher = Fetcher(session, ["api.loe.lviv.ua"])
            for url in ("https://evil.example/", "http://api.loe.lviv.ua/", "file:///etc/passwd"):
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
    from multidict import CIMultiDict

    from ua_outages.http import Response

    deep = Response("https://x.invalid/", 200, CIMultiDict(), b"[" * 1_000_000, "utf-8")
    with pytest.raises(FetchError):
        deep.json()
