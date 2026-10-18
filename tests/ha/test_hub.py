import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.ukraine_outages import get_hub
from custom_components.ukraine_outages.hub import SourceHub
from custom_components.ukraine_outages.ua_outages import DaySchedule, operator, now_kyiv


@dataclass
class FakeSource:
    name: str
    answer: dict
    delay: float = 0
    hosts: frozenset[str] = frozenset({"example.com"})

    async def fetch(self, http, now):
        await asyncio.sleep(self.delay)
        return self.answer


async def test_entries_set_up_together_share_one_hub(hass: HomeAssistant) -> None:
    hubs = await asyncio.gather(*(get_hub(hass) for _ in range(5)))
    assert all(hub is hubs[0] for hub in hubs)


async def test_slow_source_gives_way_and_answers_are_trimmed(hass: HomeAssistant) -> None:
    today = now_kyiv().date()
    days = {today + timedelta(days=n): DaySchedule(today + timedelta(days=n), None, {"1.1": (), "x": ()}) for n in range(-1, 30)}
    slow, good = FakeSource("slow", {}, delay=60), FakeSource("good", days)
    op = replace(operator("dtek-krem"), sources=(slow, good))
    with patch("custom_components.ukraine_outages.hub.SOURCE_DEADLINE", 0.05):
        result = await SourceHub(hass).fetch(op)
    assert list(result) == [today + timedelta(days=n) for n in range(3)]
    assert all(list(s.groups) == ["1.1"] for s in result.values())
