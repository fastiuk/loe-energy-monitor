from datetime import timedelta

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.loe_outages.const import CONF_GROUP, CONF_SCAN_INTERVAL, DOMAIN
from custom_components.loe_outages.loe_core import LoeError


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_fetch) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_GROUP: "4.2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "LOE 4.2"
    assert result["data"] == {CONF_GROUP: "4.2"}
    assert result["result"].unique_id == "4.2"


async def test_same_group_twice_aborts(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_GROUP: "4.2"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_cannot_connect_then_recover(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.side_effect = LoeError("down")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_GROUP: "1.1"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_fetch.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_GROUP: "1.1"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_sets_interval_and_reloads(
    hass: HomeAssistant, mock_fetch, config_entry
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.update_interval == timedelta(minutes=5)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 10}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 10}
    assert config_entry.runtime_data.update_interval == timedelta(minutes=10)
