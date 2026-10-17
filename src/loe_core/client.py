"""Async client for the (undocumented) LOE schedule API."""

from __future__ import annotations

import time
from datetime import date

import aiohttp

from .models import DaySchedule
from .parser import BASE_URL, parse_menu

SCHEDULE_URL = f"{BASE_URL}/api/menus"
USER_AGENT = "loe-energy-monitor/0.1"


class LoeError(Exception):
    """The schedule could not be fetched or did not look like a schedule."""


class LoeClient:
    def __init__(self, session: aiohttp.ClientSession, *, timeout: float = 30) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def fetch_schedule(self) -> dict[date, DaySchedule]:
        """Today's and (once published) tomorrow's schedule, keyed by day."""
        params = {
            "page": "1",
            "type": "photo-grafic",
            # Cloudflare caches this endpoint for up to 2 h (s-maxage=7200);
            # a unique query string makes it fetch a fresh copy from the origin.
            "_": str(int(time.time())),
        }
        headers = {"Accept": "application/ld+json", "User-Agent": USER_AGENT}
        try:
            async with self._session.get(
                SCHEDULE_URL, params=params, headers=headers, timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise LoeError(f"Failed to fetch schedule: {err!r}") from err
        try:
            return parse_menu(payload)
        except (ValueError, TypeError, AttributeError) as err:
            raise LoeError(f"Unexpected schedule response: {err}") from err
