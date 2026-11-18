"""Instellen van Glow Schedules: welke ploeg je deze week hebt."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import BooleanSelector, SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import CONF_RESTORE, CONF_THIS_WEEK, CONF_WEEKEND_FREE, DOMAIN

SHIFT_SELECT = SelectSelector(SelectSelectorConfig(options=["late", "early", "free"], mode=SelectSelectorMode.LIST, translation_key="shift"))


def _schema(d: dict[str, Any], first: bool) -> vol.Schema:
    fields: dict = {}
    if first:
        fields[vol.Required(CONF_THIS_WEEK, default=d.get(CONF_THIS_WEEK, "late"))] = SHIFT_SELECT
    fields[vol.Required(CONF_WEEKEND_FREE, default=d.get(CONF_WEEKEND_FREE, True))] = BooleanSelector()
    fields[vol.Required(CONF_RESTORE, default=d.get(CONF_RESTORE, True))] = BooleanSelector()
    return vol.Schema(fields)


class GlowSchedulesFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Glow Schedules", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}, True))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GlowSchedulesOptions()


class GlowSchedulesOptions(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=_schema({**self.config_entry.data, **self.config_entry.options}, False))
