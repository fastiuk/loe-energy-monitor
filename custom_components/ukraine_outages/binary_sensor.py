from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OutageCoordinator, OutagesConfigEntry
from .entity import OutageEntity, day_outages
from .ua_outages import KYIV

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: OutagesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([OutageBinarySensor(coordinator), PossibleOutageBinarySensor(coordinator)])


def _now_in(coordinator: OutageCoordinator, *, possible: bool) -> bool:
    now = dt_util.now()
    return any(o.start <= now < o.end and o.possible == possible for o in coordinator.outages())


class OutageBinarySensor(OutageEntity, BinarySensorEntity):
    """On while a scheduled outage is in progress."""

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "outage")

    @property
    def is_on(self) -> bool:
        return _now_in(self.coordinator, possible=False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        today = dt_util.now(KYIV).date()
        data = coordinator.data or {}
        group = coordinator.group
        days = {"today": data.get(today), "tomorrow": data.get(today + timedelta(days=1))}
        attributes: dict[str, Any] = {"group": group}
        for name, schedule in days.items():
            attributes[name] = day_outages(schedule, group, possible=False)
            attributes[f"{name}_possible"] = day_outages(schedule, group, possible=True)
        return attributes


class PossibleOutageBinarySensor(OutageEntity, BinarySensorEntity):
    """On while the schedule says power may be off (only some operators publish this)."""

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "possible_outage")

    @property
    def is_on(self) -> bool:
        return _now_in(self.coordinator, possible=True)
