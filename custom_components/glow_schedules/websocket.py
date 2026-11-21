"""Websocket-commando's voor de Glow Schedule Card."""
from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


def _mgr(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_delete)
    websocket_api.async_register_command(hass, ws_enable)
    websocket_api.async_register_command(hass, ws_apply)
    websocket_api.async_register_command(hass, ws_shift)


def _not_ready(connection, msg) -> None:
    connection.send_error(msg["id"], "not_ready", "Glow Schedules is not set up")


@websocket_api.websocket_command({vol.Required("type"): "glow_schedules/get"})
@callback
def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    connection.send_result(msg["id"], mgr.snapshot())


@websocket_api.websocket_command({vol.Required("type"): "glow_schedules/save", vol.Required("schedule"): dict})
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    try:
        s = await mgr.async_save_schedule(msg["schedule"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], mgr.describe(s))


@websocket_api.websocket_command({vol.Required("type"): "glow_schedules/delete", vol.Required("schedule_id"): str})
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    await mgr.async_delete_schedule(msg["schedule_id"])
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "glow_schedules/enable", vol.Required("schedule_id"): str, vol.Required("enabled"): bool})
@websocket_api.async_response
async def ws_enable(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    try:
        await mgr.async_set_enabled(msg["schedule_id"], msg["enabled"])
    except KeyError:
        connection.send_error(msg["id"], "not_found", "Unknown schedule")
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "glow_schedules/apply", vol.Required("schedule_id"): str})
@websocket_api.async_response
async def ws_apply(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    try:
        await mgr.async_apply(msg["schedule_id"])
    except KeyError:
        connection.send_error(msg["id"], "not_found", "Unknown schedule")
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "glow_schedules/shift",
    vol.Required("action"): vol.In(["set_day", "swap_week", "vacation", "config"]),
    vol.Optional("date"): str,
    vol.Optional("shift"): vol.Any(None, vol.In(["early", "late", "free", "auto"])),
    vol.Optional("on"): bool,
    vol.Optional("from"): vol.Any(None, str),
    vol.Optional("until"): vol.Any(None, str),
    vol.Optional("rotation"): [vol.In(["early", "late", "free"])],
    vol.Optional("this_week"): vol.In(["early", "late", "free"]),
    vol.Optional("weekend_free"): bool,
})
@websocket_api.async_response
async def ws_shift(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    mgr = _mgr(hass)
    if mgr is None:
        return _not_ready(connection, msg)
    try:
        a = msg["action"]
        if a == "set_day":
            d = date.fromisoformat(msg["date"]) if msg.get("date") else mgr.today()
            sh = msg.get("shift")
            await mgr.async_set_day(d, None if sh in (None, "auto") else sh)
        elif a == "swap_week":
            await mgr.async_swap_week()
        elif a == "vacation":
            await mgr.async_set_vacation(bool(msg.get("on")), msg.get("from"), msg.get("until"))
        else:
            from . import engine

            new = dict(mgr.shifts)
            if msg.get("this_week"):
                tw = msg["this_week"]
                rot = msg.get("rotation") or ([tw, "early" if tw == "late" else "late"] if tw != "free" else ["free"])
                new["rotation"] = rot
                new["anchor"] = engine.monday(mgr.today()).isoformat()
            elif msg.get("rotation"):
                new["rotation"] = msg["rotation"]
            if "weekend_free" in msg:
                new["weekend_free"] = msg["weekend_free"]
            await mgr.async_update_shifts(new)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], mgr.snapshot())
