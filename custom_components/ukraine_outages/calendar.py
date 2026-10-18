from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OutageCoordinator, OutagesConfigEntry
from .entity import OutageEntity
from .ua_outages import Outage

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: OutagesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([OutageCalendar(entry.runtime_data)])


class OutageCalendar(OutageEntity, CalendarEntity):
    """The group's outages for today and, once published, tomorrow; possible ones marked."""

    def __init__(self, coordinator: OutageCoordinator) -> None:
        super().__init__(coordinator, "schedule")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        outage = next((o for o in self.coordinator.outages() if o.end > now), None)
        return self._event(outage) if outage else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [self._event(o) for o in self.coordinator.outages() if o.end > start_date and o.start < end_date]

    def _event(self, outage: Outage) -> CalendarEvent:
        coordinator: OutageCoordinator = self.coordinator
        group = coordinator.group
        label = coordinator.operator.group_label
        if self.hass.config.language == "uk":
            what = "Можливе відключення світла" if outage.possible else "Відключення світла"
            summary = f"{what} ({label} {group})"
        else:
            what = "Possible power outage" if outage.possible else "Power outage"
            summary = f"{what} (group {group})"
        return CalendarEvent(
            start=outage.start,
            end=outage.end,
            summary=summary,
            uid=f"{coordinator.operator.id}-{group}-{outage.start.isoformat()}{'-possible' if outage.possible else ''}",
        )
