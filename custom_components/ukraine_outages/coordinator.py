"""Fetches one group's schedule and wakes entities up at outage boundaries."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_ADDRESS, CONF_GROUP, CONF_OPERATOR, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .hub import SourceHub
from .ua_outages import KYIV, DaySchedule, FetchError, Operator, Outage, group_events, group_outages, merge_schedules
from .ua_outages import operator as find_operator

_LOGGER = logging.getLogger(__name__)


type OutagesConfigEntry = ConfigEntry[OutageCoordinator]


def scan_minutes(op: Operator, requested: int) -> int:
    """The requested interval, but never more often than the operator's sources allow."""
    return max(requested, -(-op.poll_interval // 60), 1)


class OutageCoordinator(DataUpdateCoordinator[dict[date, DaySchedule]]):
    config_entry: OutagesConfigEntry

    def __init__(self, hass: HomeAssistant, entry: OutagesConfigEntry, hub: SourceHub) -> None:
        op = find_operator(entry.data[CONF_OPERATOR])
        if op is None or not op.available:
            raise ValueError(f"Unknown or unavailable operator {entry.data[CONF_OPERATOR]!r}")
        self.operator = op
        self.group: str = entry.data[CONF_GROUP]
        self.address: str | None = entry.data.get(CONF_ADDRESS)
        self.hub = hub
        minutes = scan_minutes(op, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {op.id} {self.group}",
            update_interval=timedelta(minutes=minutes),
        )
        self._unsub_transition: CALLBACK_TYPE | None = None

    @property
    def last_source(self) -> str | None:
        return self.hub.last_source.get((self.operator.id, self.address))

    async def _async_update_data(self) -> dict[date, DaySchedule]:
        try:
            fresh = await self.hub.fetch(self.operator, self.address)
        except FetchError as err:
            raise UpdateFailed(str(err)) from err
        if self.operator.by_address:
            self._follow_address_group(fresh)
        # Keeps days and groups a fetch lacks (sites sometimes serve a half-edited page)
        # and drops past days.
        return merge_schedules(self.data or {}, fresh, since=dt_util.now(KYIV).date())

    def _follow_address_group(self, fresh: dict[date, DaySchedule]) -> None:
        """The operator moved the address to another group: follow it, as the bot does."""
        groups = {g for s in fresh.values() for g in s.mentioned_groups()}
        if len(groups) != 1 or self.group in groups:
            return
        new = groups.pop()
        if new not in self.operator.groups:
            return
        _LOGGER.info("%s: the address moved from group %s to %s", self.operator.id, self.group, new)
        self.group = new
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_GROUP: new}
        )

    @property
    def schedules(self) -> list[DaySchedule]:
        return [self.data[day] for day in sorted(self.data)] if self.data else []

    def outages(self, *, possible: bool = True) -> tuple[Outage, ...]:
        """This group's outages across known days, joined across midnight."""
        return group_outages(self.schedules, self.group, possible=possible)

    @callback
    def async_update_listeners(self) -> None:
        super().async_update_listeners()
        self._schedule_transition()

    @callback
    def _schedule_transition(self) -> None:
        """Refresh entities exactly when an outage starts or ends, not at the next poll."""
        self.cancel_transition()
        now = dt_util.now()
        upcoming = [e.at for e in group_events(self.schedules, self.group, possible=True) if e.at > now]
        if upcoming:
            self._unsub_transition = async_track_point_in_time(self.hass, self._handle_transition, min(upcoming))

    @callback
    def _handle_transition(self, _now: datetime) -> None:
        self._unsub_transition = None
        self.async_update_listeners()

    @callback
    def cancel_transition(self) -> None:
        if self._unsub_transition is not None:
            self._unsub_transition()
            self._unsub_transition = None
