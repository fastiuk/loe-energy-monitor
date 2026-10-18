"""Hourly outage schedule (графік погодинних відключень) of one group, for any region of Ukraine."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import OutageCoordinator, OutagesConfigEntry
from .hub import SourceHub
from .ua_outages.http import ssl_context

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]
HUB: HassKey[SourceHub] = HassKey(DOMAIN)


async def get_hub(hass: HomeAssistant) -> SourceHub:
    """The shared hub, created on first use (config flow or the first entry)."""
    if HUB not in hass.data:
        # Reads CA bundles from disk: never on the event loop. Cached for every later request.
        await hass.async_add_executor_job(ssl_context)
        hass.data[HUB] = SourceHub(hass)
    return hass.data[HUB]


async def async_setup_entry(hass: HomeAssistant, entry: OutagesConfigEntry) -> bool:
    coordinator = OutageCoordinator(hass, entry, await get_hub(hass))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.cancel_transition)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OutagesConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
