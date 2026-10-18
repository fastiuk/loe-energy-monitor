from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ukraine_outages.const import (
    CONF_ADDRESS,
    CONF_ADDRESS_LABEL,
    CONF_CHOICE,
    CONF_GROUP,
    CONF_OPERATOR,
    CONF_QUERY,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.ukraine_outages.ua_outages import Choice, FetchError, Resolution, operator


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return result


async def _configure(hass: HomeAssistant, result, data):
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


def _lookup(operator_id: str, options, resolution: Resolution):
    lookup = type(operator(operator_id).lookup)
    return (
        patch.object(lookup, "options", AsyncMock(side_effect=options)),
        patch.object(lookup, "resolve", AsyncMock(return_value=resolution)),
    )


async def test_region_without_lookup_goes_straight_to_groups(hass: HomeAssistant, mock_fetch) -> None:
    result = await _start(hass)
    regions = [o["value"] for o in result["data_schema"].schema[CONF_REGION].config["options"]]
    assert "kyivska" in regions and "khersonska" not in regions  # no schedules there
    assert "Херсонська" in result["description_placeholders"]["unavailable"]
    assert regions[1] == "crimea" and regions[-1] == "sevastopol"

    result = await _configure(hass, result, {CONF_REGION: "kyivska"})
    assert result["step_id"] == "group"
    result = await _configure(hass, result, {CONF_GROUP: "3.2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Київська · черга 3.2"
    assert result["data"] == {CONF_OPERATOR: "dtek-krem", CONF_GROUP: "3.2"}
    assert result["result"].unique_id == "dtek-krem:3.2"


async def test_region_with_two_operators_and_the_group_menu(hass: HomeAssistant, mock_fetch) -> None:
    result = await _configure(hass, await _start(hass), {CONF_REGION: "dnipropetrovska"})
    assert result["step_id"] == "operator"
    result = await _configure(hass, result, {CONF_OPERATOR: "cek"})
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["group", "address"]
    result = await _configure(hass, result, {"next_step_id": "group"})
    result = await _configure(hass, result, {CONF_GROUP: "6.2"})
    assert result["title"] == "Дніпропетровська · Центральна енергетична компанія (ЦЕК) · черга 6.2"


async def test_group_found_by_address(hass: HomeAssistant, mock_fetch) -> None:
    streets = [Choice("1", "вул. Хрещатик"), Choice("2", "пров. Хрещатий")]
    options, resolve = _lookup("dtek-kem", [streets, [Choice("10", "1")]], Resolution(("19.1",)))
    with options as mock_options, resolve:
        result = await _configure(hass, await _start(hass), {CONF_REGION: "kyiv"})
        result = await _configure(hass, result, {"next_step_id": "address"})
        assert result["step_id"] == "address" and result["description_placeholders"]["number"] == "1"

        result = await _configure(hass, result, {CONF_QUERY: "Хрещатик"})
        assert result["step_id"] == "address_pick"
        result = await _configure(hass, result, {CONF_CHOICE: "1"})
        assert result["step_id"] == "address" and result["description_placeholders"]["chosen"] == "вул. Хрещатик"

        result = await _configure(hass, result, {CONF_QUERY: "1"})  # one match: taken right away
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "м. Київ · група 19.1"
    assert result["data"] == {CONF_OPERATOR: "dtek-kem", CONF_GROUP: "19.1"}
    assert mock_options.await_args_list[1].args[1:] == (["1"], "1")  # (http, path, query)


async def test_address_errors(hass: HomeAssistant, mock_fetch) -> None:
    options, resolve = _lookup("dtek-kem", [[], FetchError("down")], Resolution(()))
    with options, resolve:
        result = await _configure(hass, await _start(hass), {CONF_REGION: "kyiv"})
        result = await _configure(hass, result, {"next_step_id": "address"})
        result = await _configure(hass, result, {CONF_QUERY: "Неіснуюча"})
        assert result["errors"] == {CONF_QUERY: "not_found"}
        result = await _configure(hass, result, {CONF_QUERY: "Х"})
        assert result["errors"] == {CONF_QUERY: "query_too_short"}
        result = await _configure(hass, result, {CONF_QUERY: "Хрещатик"})
        assert result["errors"] == {"base": "cannot_connect"}


async def test_per_address_operator_is_subscribed_by_address(hass: HomeAssistant, mock_fetch) -> None:
    steps = [[Choice("1032", "м. Тернопіль")], [Choice("10035", "вул. Руська")], [Choice("1", "1")]]
    options, resolve = _lookup("ternopil-oe", steps, Resolution(("3.2",), address="1032/10035/1"))
    with options, resolve:
        result = await _configure(hass, await _start(hass), {CONF_REGION: "ternopilska"})
        assert result["step_id"] == "address"  # no list of groups: the source works per address
        for query in ("Тернопіль", "Руська", "1"):
            result = await _configure(hass, result, {CONF_QUERY: query})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "м. Тернопіль, вул. Руська, 1 · черга 3.2"
    assert result["data"] == {
        CONF_OPERATOR: "ternopil-oe",
        CONF_GROUP: "3.2",
        CONF_ADDRESS: "1032/10035/1",
        CONF_ADDRESS_LABEL: "м. Тернопіль, вул. Руська, 1",
    }
    assert result["result"].unique_id == "ternopil-oe:1032/10035/1"
    assert mock_fetch.await_args.args[1:] == ("1032/10035/1",)


async def test_address_in_no_group_aborts(hass: HomeAssistant, mock_fetch) -> None:
    options, resolve = _lookup("dtek-kem", [[Choice("1", "вул. А")], [Choice("2", "1")]], Resolution((), ("Не входить",)))
    with options, resolve:
        result = await _configure(hass, await _start(hass), {CONF_REGION: "kyiv"})
        result = await _configure(hass, result, {"next_step_id": "address"})
        result = await _configure(hass, result, {CONF_QUERY: "вул. А"})
        result = await _configure(hass, result, {CONF_QUERY: "1"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_group"
    assert result["description_placeholders"] == {"other": "Не входить"}


async def test_same_group_twice_aborts(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    config_entry.add_to_hass(hass)
    result = await _configure(hass, await _start(hass), {CONF_REGION: "kyivska"})
    result = await _configure(hass, result, {CONF_GROUP: "3.2"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_cannot_connect_then_retry(hass: HomeAssistant, mock_fetch) -> None:
    mock_fetch.side_effect = FetchError("down")
    result = await _configure(hass, await _start(hass), {CONF_REGION: "kyivska"})
    result = await _configure(hass, result, {CONF_GROUP: "1.1"})
    assert result["step_id"] == "retry" and result["errors"] == {"base": "cannot_connect"}

    mock_fetch.side_effect = None
    result = await _configure(hass, result, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_keeps_the_sources_minimum(hass: HomeAssistant, mock_fetch, config_entry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.update_interval == timedelta(minutes=5)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["description_placeholders"] == {"minimum": "5"}  # outage-data-ua refreshes every 5 min
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 15})
    await hass.async_block_till_done()
    assert config_entry.options == {CONF_SCAN_INTERVAL: 15}
    assert config_entry.runtime_data.update_interval == timedelta(minutes=15)


async def test_occupied_territories_are_listed_with_a_stub(hass: HomeAssistant, mock_fetch) -> None:
    for region in ("crimea", "sevastopol"):
        result = await _configure(hass, await _start(hass), {CONF_REGION: region})
        assert result["type"] is FlowResultType.ABORT and result["reason"] == "occupied"
    assert not mock_fetch.called


async def test_directory_text_is_shown_literally(hass: HomeAssistant, mock_fetch) -> None:
    """Labels come from the operator's site: Markdown in them must not become links or images."""
    hostile = "![x](https://evil.example/t.png) [тут](https://evil.example) **вул.** <img src=x>"
    options, resolve = _lookup("dtek-kem", [[Choice("1", hostile)], [Choice("2", "1")]], Resolution((), (hostile,)))
    with options, resolve:
        result = await _configure(hass, await _start(hass), {CONF_REGION: "kyiv"})
        result = await _configure(hass, result, {"next_step_id": "address"})
        result = await _configure(hass, result, {CONF_QUERY: "вул"})
        chosen = result["description_placeholders"]["chosen"]
        result = await _configure(hass, result, {CONF_QUERY: "1"})
    escaped = r"\!\[x\]\(https://evil.example/t.png\) \[тут\]\(https://evil.example\) \*\*вул.\*\* \<img src=x\>"
    assert chosen == escaped
    assert result["description_placeholders"] == {"other": escaped}
