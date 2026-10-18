from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ukraine_outages.const import CONF_ADDRESS, CONF_GROUP, CONF_OPERATOR, CONF_SCAN_INTERVAL, DOMAIN
from custom_components.ukraine_outages.diagnostics import async_get_config_entry_diagnostics
from custom_components.ukraine_outages.ua_outages import DaySchedule, FetchError, Outage

from .conftest import DAY, GROUP, OPERATOR, UNIQUE_ID, entity_id, kyiv


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

    outage = hass.states.get(entity_id(hass, "binary_sensor", "outage"))
    assert outage.state == "off"
    assert outage.attributes["group"] == GROUP
    assert outage.attributes["today"] == ["10:00–12:00", "21:00–22:30"]
    assert outage.attributes["today_possible"] == ["12:00–12:30"]
    assert outage.attributes["tomorrow"] is None
    assert outage.attributes["attribution"].startswith("Дані: оператори")

    assert hass.states.get(entity_id(hass, "sensor", "next_outage")).state == utc(kyiv(10))
    assert hass.states.get(entity_id(hass, "sensor", "power_back")).state == utc(kyiv(12))
    time_today = hass.states.get(entity_id(hass, "sensor", "outage_time_today"))
    assert float(time_today.state) == 3.5 and time_today.attributes["unit_of_measurement"] == "h"
    assert hass.states.get(entity_id(hass, "sensor", "updated")).state == utc(kyiv(8, 15))

    # Off by default: only some operators publish possible outages.
    registry = er.async_get(hass)
    assert registry.async_get(entity_id(hass, "binary_sensor", "possible_outage")).disabled_by is not None
    assert registry.async_get(entity_id(hass, "sensor", "source")).disabled_by is not None

    calendar = hass.states.get(entity_id(hass, "calendar", "schedule"))
    assert calendar.state == "off"
    assert calendar.attributes["message"] == f"Power outage (group {GROUP})"
    assert calendar.attributes["start_time"] == "2026-10-17 10:00:00"


async def test_states_flip_exactly_at_outage_boundaries(kyiv_hass, freezer, mock_fetch) -> None:
    hass = kyiv_hass
    # Poll hourly so that only the transition timer can update the state at 10:00.
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_OPERATOR: OPERATOR, CONF_GROUP: GROUP},
        options={CONF_SCAN_INTERVAL: 60},
        unique_id=UNIQUE_ID,
    )
    freezer.move_to(kyiv(9, 30))
    await _setup(hass, entry)
    outage = entity_id(hass, "binary_sensor", "outage")
    assert mock_fetch.call_count == 1

    await _move_to(hass, freezer, kyiv(10))
    assert mock_fetch.call_count == 1
    assert hass.states.get(outage).state == "on"
    assert hass.states.get(entity_id(hass, "sensor", "next_outage")).state == utc(kyiv(21))

    await _move_to(hass, freezer, kyiv(12, 10))  # inside the possible outage
    assert hass.states.get(outage).state == "off"
    assert hass.states.get(entity_id(hass, "calendar", "schedule")).state == "on"
    assert hass.states.get(entity_id(hass, "sensor", "power_back")).state == utc(kyiv(22, 30))

    await _move_to(hass, freezer, kyiv(22, 30))
    assert hass.states.get(entity_id(hass, "sensor", "next_outage")).state == "unknown"


async def test_last_schedule_kept_while_the_site_is_down(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)

    mock_fetch.side_effect = FetchError("down")
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert hass.states.get(entity_id(hass, "sensor", "next_outage")).state == utc(kyiv(10))


async def test_calendar_marks_possible_outages(kyiv_hass, freezer, mock_fetch, config_entry) -> None:
    hass = kyiv_hass
    hass.config.language = "uk"
    freezer.move_to(kyiv(9))
    await _setup(hass, config_entry)
    calendar = entity_id(hass, "calendar", "schedule")

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {"entity_id": calendar, "start_date_time": kyiv(0), "end_date_time": kyiv(0, day=18)},
        blocking=True,
        return_response=True,
    )
    events = response[calendar]["events"]
    assert [e["summary"] for e in events] == [
        f"Відключення світла (черга {GROUP})",
        f"Можливе відключення світла (черга {GROUP})",
        f"Відключення світла (черга {GROUP})",
    ]


async def test_per_address_entry_follows_its_group(kyiv_hass, freezer, mock_fetch) -> None:
    hass = kyiv_hass
    moved = {DAY: DaySchedule(DAY, None, {"4.1": (Outage(kyiv(10), kyiv(12)),)})}
    mock_fetch.return_value = moved
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="м. Тернопіль, вул. Руська, 1 · черга 3.2",
        data={CONF_OPERATOR: "ternopil-oe", CONF_GROUP: "3.2", CONF_ADDRESS: "1032/10035/1"},
        unique_id="ternopil-oe:1032/10035/1",
    )
    freezer.move_to(kyiv(9))
    await _setup(hass, entry)
    assert entry.data[CONF_GROUP] == "4.1"
    assert mock_fetch.await_args.args[1:] == ("1032/10035/1",)
    outage = entity_id(hass, "binary_sensor", "outage", "ternopil-oe:1032/10035/1")
    assert hass.states.get(outage).attributes["today"] == ["10:00–12:00"]

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entry"][CONF_ADDRESS] == "**REDACTED**"
    assert "1032/10035/1" not in str(diagnostics)


async def test_setup_retries_and_unloads(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    mock_fetch.side_effect = FetchError("down")
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
