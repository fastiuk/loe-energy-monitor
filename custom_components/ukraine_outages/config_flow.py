"""Region → operator → group, picked from a list or found by address in the operator's directory."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from . import get_hub
from .const import (
    CONF_ADDRESS,
    CONF_ADDRESS_LABEL,
    CONF_CHOICE,
    CONF_GROUP,
    CONF_OPERATOR,
    CONF_QUERY,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
)
from .coordinator import scan_minutes
from .ua_outages import REGIONS, Choice, FetchError, Operator, Region, operator
from .ua_outages.providers._address import clean_query
from .ua_outages.registry import REGIONS_BY_ID


#: Characters with a meaning in the Markdown HA renders form descriptions with, and what
#: starts a bare URL it turns into a link: the ":" of "://", the "." after "www".
_MARKDOWN_RE = re.compile(r"([\\`*_{}\[\]()<>#+!|~&]|:(?=//)|(?<=www)\.)", re.IGNORECASE)


def _md(text: str) -> str:
    """Text from an operator's directory, shown literally: no links, images or markup.

    Each of those characters gets a backslash, which the renderer drops: "https\\://x" and
    "www\\.x" show as "https://x" and "www.x", but not as links.
    """
    return _MARKDOWN_RE.sub(r"\\\1", text)


def _operators(region: Region) -> list[Operator]:
    """The region's operators that have schedules."""
    return [op for op_id in region.operators if (op := operator(op_id)) and op.available]


def _dropdown(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


class OutagesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._region: Region | None = None
        self._operator: Operator | None = None
        self._path: list[Choice] = []
        self._choices: list[Choice] = []
        #: Groups to choose from after an address search found several.
        self._groups: tuple[str, ...] = ()
        self._address: str | None = None
        self._address_label: str | None = None
        self._group: str | None = None

    # Region and operator

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Occupied territories are listed too, so everyone finds their home; they get a stub.
        regions = [r for r in REGIONS if r.occupied or _operators(r)]
        if user_input is not None and (region := REGIONS_BY_ID.get(user_input[CONF_REGION])) in regions:
            if region.occupied:
                return self.async_abort(reason="occupied", description_placeholders={"region": region.name})
            self._region = region
            operators = _operators(region)
            if len(operators) == 1:
                self._operator = operators[0]
                return await self.async_step_method()
            return await self.async_step_operator()
        schema = vol.Schema(
            {vol.Required(CONF_REGION): _dropdown([SelectOptionDict(value=r.id, label=r.name) for r in regions])}
        )
        unavailable = ", ".join(r.name for r in REGIONS if not r.occupied and not _operators(r))
        return self.async_show_form(
            step_id="user", data_schema=schema, description_placeholders={"unavailable": unavailable}
        )

    async def async_step_operator(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._region is not None
        operators = _operators(self._region)
        if user_input is not None and (op := operator(user_input[CONF_OPERATOR])) in operators:
            self._operator = op
            return await self.async_step_method()
        schema = vol.Schema(
            {vol.Required(CONF_OPERATOR): _dropdown([SelectOptionDict(value=o.id, label=o.name) for o in operators])}
        )
        return self.async_show_form(
            step_id="operator", data_schema=schema, description_placeholders={"region": self._region.name}
        )

    async def async_step_method(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        op = self._op
        if op.by_address:
            return await self.async_step_address()
        if op.lookup is None:
            return await self.async_step_group()
        return self.async_show_menu(
            step_id="method", menu_options=["group", "address"], description_placeholders=self._placeholders()
        )

    # Group from a list

    async def async_step_group(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        op = self._op
        groups = self._groups or op.groups
        if user_input is not None and user_input[CONF_GROUP] in groups:
            return await self._finish(user_input[CONF_GROUP])
        label = op.group_label.capitalize()
        schema = vol.Schema(
            {vol.Required(CONF_GROUP): _dropdown([SelectOptionDict(value=g, label=f"{label} {g}") for g in groups])}
        )
        return self.async_show_form(step_id="group", data_schema=schema, description_placeholders=self._placeholders())

    # Group by address

    async def async_step_address(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        op = self._op
        lookup = op.lookup
        assert lookup is not None
        step = lookup.steps[len(self._path)]
        errors: dict[str, str] = {}
        if user_input is not None:
            query = clean_query(str(user_input.get(CONF_QUERY) or ""))
            if not query and not step.browse:
                errors[CONF_QUERY] = "query_required"
            elif query and len(query) < lookup.min_query and not step.house:
                errors[CONF_QUERY] = "query_too_short"
            else:
                hub = await get_hub(self.hass)
                try:
                    choices = await lookup.options(hub.lookup_fetcher(op), self._ids, query)
                except (FetchError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    if not choices:
                        errors[CONF_QUERY] = "not_found"
                    elif len(choices) == 1:
                        return await self._chosen(choices[0])
                    else:
                        self._choices = choices
                        return await self.async_step_address_pick()
        key = vol.Optional(CONF_QUERY) if step.browse else vol.Required(CONF_QUERY)
        return self.async_show_form(
            step_id="address",
            data_schema=vol.Schema({key: TextSelector()}),
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                "prompt": step.prompt,
                "example": step.example or "—",
                "chosen": _md(self._chosen_label()) or "—",
                "number": str(len(self._path) + 1),
                "steps": str(len(lookup.steps)),
            },
        )

    async def async_step_address_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None and (choice := next((c for c in self._choices if c.id == user_input[CONF_CHOICE]), None)):
            return await self._chosen(choice)
        schema = vol.Schema(
            {vol.Required(CONF_CHOICE): _dropdown([SelectOptionDict(value=c.id, label=c.label) for c in self._choices])}
        )
        return self.async_show_form(
            step_id="address_pick",
            data_schema=schema,
            description_placeholders={"count": str(len(self._choices)), "chosen": _md(self._chosen_label()) or "—"},
        )

    async def _chosen(self, choice: Choice) -> ConfigFlowResult:
        op = self._op
        lookup = op.lookup
        assert lookup is not None
        self._path.append(choice)
        if len(self._path) < len(lookup.steps):
            return await self.async_step_address()
        hub = await get_hub(self.hass)
        try:
            resolution = await lookup.resolve(hub.lookup_fetcher(op), self._ids)
        except (FetchError, ValueError):
            self._path.pop()
            return self.async_abort(reason="cannot_connect")
        groups = tuple(g for g in resolution.groups if g in op.groups)
        if not groups and resolution.reason:
            return self.async_abort(reason="no_group_now", description_placeholders={"reason": _md(resolution.reason)})
        if not groups or (op.by_address and not resolution.address):
            return self.async_abort(
                reason="no_group", description_placeholders={"other": _md(", ".join(resolution.other)) or "—"}
            )
        self._address = resolution.address if op.by_address else None
        self._address_label = self._chosen_label()
        if len(groups) == 1:
            return await self._finish(groups[0])
        self._groups = groups
        return await self.async_step_group()

    # Done

    async def async_step_retry(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The schedule could not be loaded: try again on submit."""
        assert self._group is not None
        if user_input is not None:
            return await self._finish(self._group)
        return self.async_show_form(step_id="retry", errors={"base": "cannot_connect"})

    async def _finish(self, group: str) -> ConfigFlowResult:
        op = self._op
        self._group = group
        await self.async_set_unique_id(f"{op.id}:{self._address}" if op.by_address else f"{op.id}:{group}")
        self._abort_if_unique_id_configured()
        hub = await get_hub(self.hass)
        try:
            await hub.fetch(op, self._address)
        except FetchError:
            return await self.async_step_retry()
        data: dict[str, Any] = {CONF_OPERATOR: op.id, CONF_GROUP: group}
        if op.by_address:
            data[CONF_ADDRESS] = self._address
            data[CONF_ADDRESS_LABEL] = self._address_label
        return self.async_create_entry(title=entry_title(op, group, self._address_label if op.by_address else None), data=data)

    # Helpers

    @property
    def _op(self) -> Operator:
        assert self._operator is not None
        return self._operator

    @property
    def _ids(self) -> list[str]:
        return [c.id for c in self._path]

    def _chosen_label(self) -> str:
        lookup = self._op.lookup
        steps = lookup.steps if lookup else ()
        return ", ".join(c.label for c, s in zip(self._path, steps) if s.in_label)

    def _placeholders(self) -> dict[str, str]:
        op = self._op
        return {"operator": op.name, "site": op.site or "—", "note": op.note or ""}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OutagesOptionsFlow()


def entry_title(op: Operator, group: str, address_label: str | None = None) -> str:
    """"м. Київ · група 19.1", "Дніпропетровська · ЦЕК · черга 3.2", "Тернопіль, вул. Руська, 1 · черга 3.2"."""
    region = REGIONS_BY_ID[op.region]
    where = address_label or (region.name if len(region.operators) == 1 else f"{region.name} · {op.name}")
    return f"{where} · {op.group_label} {group}"


class OutagesOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        op = operator(self.config_entry.data[CONF_OPERATOR])
        minimum = scan_minutes(op, 1) if op else 1
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: max(minimum, int(user_input[CONF_SCAN_INTERVAL]))})
        current = max(minimum, self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=minimum, max=MAX_SCAN_INTERVAL, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, description_placeholders={"minimum": str(minimum)})
