"""Zakarpattiaoblenergo: the inneti platform (as Volyn), which applies no hourly schedules now.

``/api/options`` on ``api-outage-zakarpat-energy.inneti.net`` holds ``pw_gpv_image_today`` and
``pw_gpv_image_tomorrow``: paths of schedule images, ``""`` while there are none. On 20.10.2026
both were empty (the operator's news: the schedules for 01.10–01.04 «наразі НЕ ДІЮТЬ») and there
is no archive to tell an image's day, as Volyn has. So an empty answer means nothing published;
an image, whose layout is not known yet (1038×265 on the operator's page), fails loudly: the
bot's admin hears about it and the parser is written from the first real image, instead of
guessing the hours or the day.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..http import Fetcher
from ..models import DaySchedule
from .base import Provider, fail, url_on_host

BASE_URL = "https://api-outage-zakarpat-energy.inneti.net"
OPTION_KEYS = ("pw_gpv_image_today", "pw_gpv_image_tomorrow")
OPTIONS_URL = f"{BASE_URL}/api/options?" + "&".join(f"option_key[]={key}" for key in OPTION_KEYS)
HEADERS = {"Accept": "application/ld+json"}


def published_images(payload: Any) -> list[str]:
    """Paths of the schedule images on show; empty while none is."""
    members = payload.get("hydra:member") if isinstance(payload, dict) else None
    if not isinstance(members, list) or len(members) > 100:
        fail("Zakarpattia: options response has no hydra:member list")
    values = {
        m["option_key"]: m.get("value")
        for m in members
        if isinstance(m, dict) and m.get("option_key") in OPTION_KEYS
    }
    if not values:
        fail("Zakarpattia: no schedule image options")
    paths = []
    for key in OPTION_KEYS:
        value = values.get(key)
        if value in (None, ""):
            continue
        if url_on_host(BASE_URL, value) is None:
            fail(f"Zakarpattia: {key} is not a plain path: {str(value)[:80]!r}")
        paths.append(value)
    return paths


class Zakarpattia(Provider):
    hosts = frozenset({"api-outage-zakarpat-energy.inneti.net"})
    min_interval = 600

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        if images := published_images((await http.get(OPTIONS_URL, headers=HEADERS)).json()):
            fail(f"Zakarpattia: a schedule image is published ({BASE_URL}{images[0]}); its format is not known yet")
        return {}
