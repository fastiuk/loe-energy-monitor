from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import OutageCoordinator, OutagesConfigEntry
from .entity import OutageEntity
from .ua_outages import KYIV, current_outage, next_outage

PARALLEL_UPDATES = 0


def _next_outage_start(c: OutageCoordinator, now: datetime) -> datetime | None:
    outage = next_outage(c.schedules, c.group, now)
    return outage.start if outage else None


def _power_back(c: OutageCoordinator, now: datetime) -> datetime | None:
    """End of the outage in progress, or of the next one."""
    outage = current_outage(c.schedules, c.group, now) or next_outage(c.schedules, c.group, now)
    return outage.end if outage else None


def _outage_minutes_today(c: OutageCoordinator, now: datetime) -> float | None:
    schedule = (c.data or {}).get(now.astimezone(KYIV).date())
    if schedule is None:
        return None
    total = sum((o.duration for o in schedule.outages(c.group) if not o.possible), timedelta())
    return total.total_seconds() / 60


def _updated_at(c: OutageCoordinator, now: datetime) -> datetime | None:
    return max((s.updated_at for s in c.schedules if s.updated_at), default=None)


def _source(c: OutageCoordinator, now: datetime) -> str | None:
    return c.last_source


@dataclass(frozen=True, kw_only=True)
class OutageSensorDescription(SensorEntityDescription):
    value_fn: Callable[[OutageCoordinator, datetime], datetime | float | str | None]


SENSORS = (
    OutageSensorDescription(key="next_outage", device_class=SensorDeviceClass.TIMESTAMP, value_fn=_next_outage_start),
    OutageSensorDescription(key="power_back", device_class=SensorDeviceClass.TIMESTAMP, value_fn=_power_back),
    OutageSensorDescription(
        key="outage_time_today",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value_fn=_outage_minutes_today,
    ),
    OutageSensorDescription(
        key="updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_updated_at,
    ),
    OutageSensorDescription(
        key="source",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_source,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: OutagesConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(OutageSensor(entry.runtime_data, description) for description in SENSORS)


class OutageSensor(OutageEntity, SensorEntity):
    entity_description: OutageSensorDescription

    def __init__(self, coordinator: OutageCoordinator, description: OutageSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> datetime | float | str | None:
        return self.entity_description.value_fn(self.coordinator, dt_util.now())
