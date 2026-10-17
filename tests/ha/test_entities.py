from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.loe_outages.const import CONF_GROUP, CONF_SCAN_INTERVAL, DOMAIN
from custom_components.loe_outages.loe_core import LoeError

from .conftest import GROUP, kyiv

OUTAGE = "binary_sensor.loe_4_2_outage_now"
CALENDAR = "calendar.loe_4_2_outage_schedule"
NEXT_OUTAGE = "sensor.loe_4_2_next_outage"
POWER_BACK = "sensor.loe_4_2_power_back"
TIME_TODAY = "sensor.loe_4_2_outage_time_today"
UPDATED = "sensor.loe_4_2_schedule_updated"


def utc(moment) -> str:
    return dt_util.as_utc(moment).isoformat()


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _move_to(hass: HomeAssistant, freezer, moment) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done()


async def test_states_before_first_outage(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)

    outage = hass.states.get(OUTAGE)
    assert outage.state == "off"
    assert outage.attributes["today"] == ["10:00–12:00", "21:00–22:30"]
    assert outage.attributes["tomorrow"] is None
    assert outage.attributes["attribution"].startswith("Дані: Львівобленерго")

    assert hass.states.get(NEXT_OUTAGE).state == utc(kyiv(10))
    assert hass.states.get(POWER_BACK).state == utc(kyiv(12))
    assert float(hass.states.get(TIME_TODAY).state) == 3.5
    assert hass.states.get(TIME_TODAY).attributes["unit_of_measurement"] == "h"
    assert hass.states.get(UPDATED).state == utc(kyiv(10, 59))

    calendar = hass.states.get(CALENDAR)
    assert calendar.state == "off"
    assert calendar.attributes["message"] == f"Power outage (group {GROUP})"
    assert calendar.attributes["start_time"] == "2026-10-17 10:00:00"
    assert calendar.attributes["end_time"] == "2026-10-17 12:00:00"


async def test_states_flip_exactly_at_outage_boundaries(kyiv_hass, freezer, mock_fetch) -> None:
    hass = kyiv_hass
    # Poll hourly so that only the transition timer can update the state at 10:00.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_GROUP: GROUP},
        options={CONF_SCAN_INTERVAL: 60},
        unique_id=GROUP,
    )
    freezer.move_to(kyiv(9, 30))
    await _setup(hass, entry)
    assert mock_fetch.call_count == 1

    await _move_to(hass, freezer, kyiv(10))
    assert mock_fetch.call_count == 1
    assert hass.states.get(OUTAGE).state == "on"
    assert hass.states.get(CALENDAR).state == "on"
    assert hass.states.get(NEXT_OUTAGE).state == utc(kyiv(21))
    assert hass.states.get(POWER_BACK).state == utc(kyiv(12))

    await _move_to(hass, freezer, kyiv(12))
    assert hass.states.get(OUTAGE).state == "off"
    assert hass.states.get(POWER_BACK).state == utc(kyiv(22, 30))

    await _move_to(hass, freezer, kyiv(22, 30))
    assert hass.states.get(OUTAGE).state == "off"
    assert hass.states.get(NEXT_OUTAGE).state == "unknown"
    assert hass.states.get(POWER_BACK).state == "unknown"


async def test_last_schedule_kept_while_api_is_down(
    kyiv_hass, freezer, mock_fetch, config_entry
) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)

    mock_fetch.side_effect = LoeError("down")
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert hass.states.get(OUTAGE).state == "off"
    assert hass.states.get(NEXT_OUTAGE).state == utc(kyiv(10))


async def test_calendar_events(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    hass.config.language = "uk"
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": CALENDAR, "start_date_time": kyiv(0), "end_date_time": kyiv(0, day=18)},
        blocking=True,
        return_response=True,
    )
    events = response[CALENDAR]["events"]
    assert [
        (dt_util.parse_datetime(e["start"]), dt_util.parse_datetime(e["end"])) for e in events
    ] == [(kyiv(10), kyiv(12)), (kyiv(21), kyiv(22, 30))]
    assert events[0]["summary"] == f"Відключення світла (група {GROUP})"


async def test_setup_retries_and_unloads(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    mock_fetch.side_effect = LoeError("down")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY

    mock_fetch.side_effect = None
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED
