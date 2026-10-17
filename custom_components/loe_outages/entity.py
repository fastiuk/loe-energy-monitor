from __future__ import annotations

from datetime import date

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, SITE_URL
from .coordinator import LoeCoordinator
from .loe_core import KYIV, DaySchedule, Outage


class LoeEntity(CoordinatorEntity[LoeCoordinator]):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: LoeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        group = coordinator.group
        self._attr_unique_id = f"{group}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, group)},
            name=f"LOE {group}",
            manufacturer="Львівобленерго",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=SITE_URL,
        )

    @property
    def available(self) -> bool:
        # A published schedule stays valid while LOE's API is unreachable,
        # which often happens exactly during outages.
        return self.coordinator.data is not None


def fmt_outage(outage: Outage, day: date) -> str:
    """'10:00–12:00'; an outage ending at midnight reads '22:00–24:00'."""
    start, end = outage.start.astimezone(KYIV), outage.end.astimezone(KYIV)
    end_text = "24:00" if end.date() > day and end.hour == end.minute == 0 else f"{end:%H:%M}"
    return f"{start:%H:%M}–{end_text}"


def day_outages(schedule: DaySchedule | None, group: str) -> list[str] | None:
    """A day's outages as text; ``None`` if that day is not published."""
    if schedule is None:
        return None
    return [fmt_outage(o, schedule.day) for o in schedule.outages(group)]
