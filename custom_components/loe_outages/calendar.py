from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import LoeConfigEntry, LoeCoordinator
from .entity import LoeEntity
from .loe_core import Outage

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: LoeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LoeCalendar(entry.runtime_data)])


class LoeCalendar(LoeEntity, CalendarEntity):
    """Scheduled outages of the group for today and, once published, tomorrow."""

    def __init__(self, coordinator: LoeCoordinator) -> None:
        super().__init__(coordinator, "schedule")

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        outage = next((o for o in self.coordinator.outages() if o.end > now), None)
        return self._event(outage) if outage else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            self._event(o)
            for o in self.coordinator.outages()
            if o.end > start_date and o.start < end_date
        ]

    def _event(self, outage: Outage) -> CalendarEvent:
        group = self.coordinator.group
        if self.hass.config.language == "uk":
            summary = f"Відключення світла (група {group})"
        else:
            summary = f"Power outage (group {group})"
        return CalendarEvent(
            start=outage.start,
            end=outage.end,
            summary=summary,
            uid=f"loe-{group}-{outage.start.isoformat()}",
        )
