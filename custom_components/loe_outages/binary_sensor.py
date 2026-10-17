from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import LoeConfigEntry, LoeCoordinator
from .entity import LoeEntity, day_outages
from .loe_core import KYIV, current_outage

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: LoeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LoeOutageBinarySensor(entry.runtime_data)])


class LoeOutageBinarySensor(LoeEntity, BinarySensorEntity):
    """On while a scheduled outage is in progress."""

    def __init__(self, coordinator: LoeCoordinator) -> None:
        super().__init__(coordinator, "outage")

    @property
    def is_on(self) -> bool:
        coordinator = self.coordinator
        return current_outage(coordinator.schedules, coordinator.group, dt_util.now()) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        today = dt_util.now(KYIV).date()
        data = coordinator.data or {}
        return {
            "today": day_outages(data.get(today), coordinator.group),
            "tomorrow": day_outages(data.get(today + timedelta(days=1)), coordinator.group),
        }
