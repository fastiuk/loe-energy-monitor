from collections.abc import Generator
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ukraine_outages.const import CONF_GROUP, CONF_OPERATOR, DOMAIN
from custom_components.ukraine_outages.ua_outages import KYIV, DaySchedule, Outage

DAY = date(2026, 10, 17)
OPERATOR = "dtek-krem"
GROUP = "3.2"
UNIQUE_ID = f"{OPERATOR}:{GROUP}"


def kyiv(hour: int, minute: int = 0, day: int = 17) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KYIV)


def schedules() -> dict[date, DaySchedule]:
    """3.2: 10:00–12:00, maybe 12:00–12:30, 21:00–22:30; 1.1 has power all day."""
    return {
        DAY: DaySchedule(
            DAY,
            kyiv(8, 15),
            {
                GROUP: (
                    Outage(kyiv(10), kyiv(12)),
                    Outage(kyiv(12), kyiv(12, 30), possible=True),
                    Outage(kyiv(21), kyiv(22, 30)),
                ),
                "1.1": (),
            },
        )
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


@pytest.fixture
async def kyiv_hass(hass: HomeAssistant) -> HomeAssistant:
    await hass.config.async_set_time_zone("Europe/Kyiv")
    return hass


@pytest.fixture
def mock_fetch() -> Generator[AsyncMock]:
    with patch(
        "custom_components.ukraine_outages.hub.SourceHub.fetch", AsyncMock(return_value=schedules())
    ) as mock:
        yield mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Київська · черга 3.2",
        data={CONF_OPERATOR: OPERATOR, CONF_GROUP: GROUP},
        unique_id=UNIQUE_ID,
    )


def entity_id(hass: HomeAssistant, platform: str, key: str, unique_id: str = UNIQUE_ID) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{unique_id}_{key}")
    assert found, f"no {platform} {key}"
    return found


__all__ = ["DAY", "GROUP", "OPERATOR", "UNIQUE_ID", "entity_id", "kyiv", "schedules", "timedelta"]
