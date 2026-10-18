"""The LOE API is untrusted input: hostile or broken responses must stay harmless."""

import asyncio
import json
import logging
import time

import aiohttp
import pytest
from aiohttp import web

from loe_core import LoeClient, LoeError, parse_day, parse_menu
from loe_core.client import MAX_RESPONSE_BYTES
from loe_core.parser import BASE_URL, MAX_NOTES, MAX_RAW_HTML, MAX_SENTENCE

HEADER = "<p>Графік погодинних відключень на 17.10.2026</p>"


@pytest.mark.parametrize(
    "raw",
    [
        "<p" * (MAX_RAW_HTML // 2),  # tags that never close
        "<" * MAX_RAW_HTML,
        "графік " * (MAX_RAW_HTML // 7),  # header word without a date
        HEADER + "Група 1.1. з 1" * ((MAX_RAW_HTML - 60) // 14),  # many groups, one line
        HEADER + ("з 10:00 до " * 9000)[: MAX_RAW_HTML - 60],
    ],
)
def test_hostile_text_is_parsed_in_linear_time(raw):
    assert len(raw) <= MAX_RAW_HTML
    started = time.perf_counter()
    parse_day(raw)
    assert time.perf_counter() - started < 0.5


def test_oversized_schedule_text_is_ignored():
    assert parse_day(HEADER + "x" * MAX_RAW_HTML) is None


def test_unknown_text_and_notes_are_bounded(caplog):
    raw = HEADER + "".join(f"<p>Група {q}.{s}. {'?' * 1000}</p>" for q in range(1, 7) for s in (1, 2))
    raw += "".join(f"<p>примітка {i} {'!' * 1000}</p>" for i in range(50))
    with caplog.at_level(logging.WARNING, logger="loe_core.parser"):
        schedule = parse_day(raw)
    assert len(schedule.unknown) == 12
    assert all(len(text) <= MAX_SENTENCE for text in schedule.unknown.values())
    assert len(schedule.notes) == MAX_NOTES
    assert all(len(note) <= MAX_SENTENCE for note in schedule.notes)
    assert len(caplog.records) == 1  # one summary line, not one per group


def _menu(image_url):
    item = {"rawHtml": HEADER + "<p>Група 1.1. Електроенергія є.</p>", "imageUrl": image_url}
    return {"hydra:member": [{"type": "photo-grafic", "menuItems": [item]}]}


@pytest.mark.parametrize(
    "image_url",
    [
        "@evil.example/x.png",  # https://api.loe.lviv.ua@evil.example/x.png
        "//evil.example/x.png",
        "https://evil.example/x.png",
        "/media//evil.example/x.png",
        "/media/x.png?redirect=https://evil.example",
        "/media/x.png\nfoo",
        None,
        42,
        "",
    ],
)
def test_image_url_cannot_point_to_another_host(image_url):
    (schedule,) = parse_menu(_menu(image_url)).values()
    assert schedule.image_url is None


def test_image_url_on_the_api_host_is_kept():
    (schedule,) = parse_menu(_menu("/media/6ad32af94506a_GPV.png")).values()
    assert schedule.image_url == f"{BASE_URL}/media/6ad32af94506a_GPV.png"


async def _fetch_from(handler):
    app = web.Application()
    app.router.add_get("/api/menus", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with aiohttp.ClientSession() as session:
            client = LoeClient(session, url=f"http://127.0.0.1:{port}/api/menus")
            return await client.fetch_schedule()
    finally:
        await runner.cleanup()


def test_client_rejects_oversized_response():
    async def huge(request):
        response = web.StreamResponse()
        await response.prepare(request)
        chunk = b" " * (1024 * 1024)
        for _ in range(MAX_RESPONSE_BYTES // len(chunk) + 2):
            await response.write(chunk)
        return response

    with pytest.raises(LoeError, match="over"):
        asyncio.run(_fetch_from(huge))


def test_client_rejects_non_json_and_wrong_shapes():
    async def html(request):
        return web.Response(text="<html>Cloudflare challenge</html>")

    async def a_list(request):
        return web.json_response([1, 2, 3])

    for handler in (html, a_list):
        with pytest.raises(LoeError):
            asyncio.run(_fetch_from(handler))


def test_client_reads_a_normal_response():
    async def ok(request):
        return web.Response(text=json.dumps(_menu("/media/a.png")))

    schedules = asyncio.run(_fetch_from(ok))
    assert len(schedules) == 1
