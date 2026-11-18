"""Glow Schedules: schema's voor Home Assistant die de ploeg volgen (vroeg, laat, vrij)."""
from __future__ import annotations

from datetime import date  # noqa: F401

from homeassistant.util import dt as dt_util

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import engine
from .const import CONF_RESTORE, CONF_THIS_WEEK, CONF_WEEKEND_FREE, DOMAIN, PLATFORMS
from .manager import ScheduleManager
from .websocket import async_register_websocket

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SHIFT_OR_AUTO = vol.In(["early", "late", "free", "auto"])


def _manager(hass: HomeAssistant) -> ScheduleManager:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Glow Schedules is not set up")
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_websocket(hass)

    async def set_day_shift(call: ServiceCall) -> None:
        d = call.data.get("date") or _manager(hass).today()
        shift = call.data["shift"]
        await _manager(hass).async_set_day(d, None if shift == "auto" else shift)

    async def swap_week(call: ServiceCall) -> None:
        await _manager(hass).async_swap_week()

    async def set_vacation(call: ServiceCall) -> None:
        f = call.data.get("from")
        u = call.data.get("until")
        await _manager(hass).async_set_vacation(call.data["on"], f.isoformat() if f else None, u.isoformat() if u else None)

    hass.services.async_register(DOMAIN, "set_day_shift", set_day_shift, schema=vol.Schema({vol.Optional("date"): cv.date, vol.Required("shift"): SHIFT_OR_AUTO}))
    hass.services.async_register(DOMAIN, "swap_week", swap_week)
    hass.services.async_register(DOMAIN, "set_vacation", set_vacation, schema=vol.Schema({vol.Required("on"): cv.boolean, vol.Optional("from"): cv.date, vol.Optional("until"): cv.date}))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    opts = {**entry.data, **entry.options}
    today = dt_util.now().date()
    this_week = opts.get(CONF_THIS_WEEK, "late")
    other = "early" if this_week == "late" else "late"
    initial = {
        "rotation": [this_week, other],
        "anchor": engine.monday(today).isoformat(),
        "weekend_free": opts.get(CONF_WEEKEND_FREE, True),
        "overrides": {},
        "vacation": {"on": False},
    }
    mgr = ScheduleManager(hass, entry.entry_id, {"restore": opts.get(CONF_RESTORE, True)})
    await mgr.async_load(initial)
    # een weekendinstelling uit de opties wint van de opslag
    if CONF_WEEKEND_FREE in entry.options and mgr.shifts.get("weekend_free") != entry.options[CONF_WEEKEND_FREE]:
        mgr.shifts = {**mgr.shifts, "weekend_free": entry.options[CONF_WEEKEND_FREE]}
    entry.runtime_data = mgr
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await mgr.async_start()
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok
