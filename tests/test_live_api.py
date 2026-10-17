"""Checks against the real LOE API, run daily in CI to notice when it changes.

Skipped unless LOE_LIVE=1, so the regular test run stays offline.
"""

import asyncio
import os
import re
import time
import warnings
from datetime import timedelta

import aiohttp
import pytest

from loe_core import GROUPS, LoeClient, now_kyiv, parse_day
from loe_core.client import SCHEDULE_URL, USER_AGENT

pytestmark = pytest.mark.skipif(
    os.environ.get("LOE_LIVE") != "1", reason="set LOE_LIVE=1 to query the real LOE API"
)

SITE = "https://poweron.loe.lviv.ua"
TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _get(session: aiohttp.ClientSession, url: str, **params: str) -> aiohttp.ClientResponse:
    resp = await session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def test_site_still_reads_this_endpoint():
    """The old endpoint may keep answering after the site moves elsewhere; the bundle tells."""

    async def bundle() -> str:
        async with aiohttp.ClientSession() as session:
            index = await (await _get(session, f"{SITE}/")).text()
            script = re.search(r'src="(/static/js/main\.[^"]+\.js)"', index)
            assert script, "poweron.loe.lviv.ua no longer loads /static/js/main.*.js"
            return await (await _get(session, SITE + script[1])).text()

    js = asyncio.run(bundle())
    assert "https://api.loe.lviv.ua/api" in js, "site no longer uses api.loe.lviv.ua"
    assert "photo-grafic" in js, "site no longer reads the photo-grafic menu"


def test_schedule_menu_format():
    async def menu() -> dict:
        async with aiohttp.ClientSession() as session:
            resp = await _get(
                session, SCHEDULE_URL, page="1", type="photo-grafic", _=str(int(time.time()))
            )
            return await resp.json(content_type=None)

    payload = asyncio.run(menu())
    schedule_menu = next(m for m in payload["hydra:member"] if m.get("type") == "photo-grafic")
    items = schedule_menu["menuItems"]
    assert {"Today", "Tomorrow"} <= {item.get("name") for item in items}

    for item in items:
        raw_html = item.get("rawHtml") or ""
        if not raw_html.strip():
            continue
        schedule = parse_day(raw_html)
        assert schedule is not None, f"{item['name']}: no date header in {raw_html[:300]!r}"
        assert schedule.unknown == {}, f"{item['name']}: unrecognised sentences {schedule.unknown}"
        if schedule.groups:
            assert set(schedule.groups) == set(GROUPS), f"{item['name']}: groups {sorted(schedule.groups)}"


def test_client_fetches_schedule():
    async def fetch():
        async with aiohttp.ClientSession() as session:
            return await LoeClient(session).fetch_schedule()

    schedules = asyncio.run(fetch())
    today = now_kyiv().date()
    lines = [
        f"- {day:%d.%m.%Y}: {len(s.groups)} груп, оновлено "
        f"{s.updated_at:%H:%M %d.%m}" if s.updated_at else f"- {day:%d.%m.%Y}: {len(s.groups)} груп"
        for day, s in sorted(schedules.items())
    ] or ["- графіків немає"]
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Графіки на сайті ЛОЕ\n" + "\n".join(lines) + "\n")

    # No schedule for days is normal when outages are not applied, so only warn.
    if not schedules or max(schedules) < today - timedelta(days=2):
        warnings.warn(f"No fresh LOE schedule: {', '.join(map(str, schedules)) or 'none'}")
