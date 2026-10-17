"""Fetches the LOE schedule and wakes entities up at outage boundaries."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_GROUP, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .loe_core import (
    KYIV,
    DaySchedule,
    LoeClient,
    LoeError,
    Outage,
    group_events,
    group_outages,
    merge_schedules,
)

_LOGGER = logging.getLogger(__name__)

type LoeConfigEntry = ConfigEntry[LoeCoordinator]


class LoeCoordinator(DataUpdateCoordinator[dict[date, DaySchedule]]):
    config_entry: LoeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: LoeConfigEntry) -> None:
        self.group: str = entry.data[CONF_GROUP]
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.group}",
            update_interval=timedelta(minutes=minutes),
        )
        self._client = LoeClient(async_get_clientsession(hass))
        self._unsub_transition: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> dict[date, DaySchedule]:
        try:
            fresh = await self._client.fetch_schedule()
        except LoeError as err:
            raise UpdateFailed(str(err)) from err
        # Keeps days and groups a fetch lacks (LOE sometimes serves a half-edited
        # page) and drops past days.
        return merge_schedules(self.data or {}, fresh, since=dt_util.now(KYIV).date())

    @property
    def schedules(self) -> list[DaySchedule]:
        return [self.data[day] for day in sorted(self.data)] if self.data else []

    def outages(self) -> tuple[Outage, ...]:
        """This group's outages across known days, joined across midnight."""
        return group_outages(self.schedules, self.group)

    @callback
    def async_update_listeners(self) -> None:
        super().async_update_listeners()
        self._schedule_transition()

    @callback
    def _schedule_transition(self) -> None:
        """Refresh entities exactly when the next outage starts or ends, not at the next poll."""
        self.cancel_transition()
        now = dt_util.now()
        upcoming = [e.at for e in group_events(self.schedules, self.group) if e.at > now]
        if upcoming:
            self._unsub_transition = async_track_point_in_time(
                self.hass, self._handle_transition, min(upcoming)
            )

    @callback
    def _handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        self.async_update_listeners()

    @callback
    def cancel_transition(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None
