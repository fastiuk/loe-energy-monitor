import json
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.loe_outages.const import CONF_GROUP, DOMAIN
from custom_components.loe_outages.loe_core import KYIV, parse_menu

FIXTURE = Path(__file__).parent.parent / "fixtures" / "photo_grafic_2026-10-17.json"
# Group 4.2 in the fixture: 10:00–12:00 and 21:00–22:30 on 17.10.2026.
GROUP = "4.2"


def kyiv(hour: int, minute: int = 0, day: int = 17) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KYIV)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


@pytest.fixture
async def kyiv_hass(hass: HomeAssistant) -> HomeAssistant:
    await hass.config.async_set_time_zone("Europe/Kyiv")
    return hass


@pytest.fixture
def schedule():
    return parse_menu(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def mock_fetch(schedule) -> Generator[AsyncMock]:
    with patch(
        "custom_components.loe_outages.loe_core.client.LoeClient.fetch_schedule",
        AsyncMock(return_value=schedule),
    ) as mock:
        yield mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title=f"LOE {GROUP}", data={CONF_GROUP: GROUP}, unique_id=GROUP
    )
