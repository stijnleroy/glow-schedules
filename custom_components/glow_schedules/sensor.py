"""Sensor met de ploeg van vandaag."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import engine
from .const import DOMAIN, SIGNAL_UPDATE


def device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Glow Schedules", manufacturer="Glow", model="Schedules")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ShiftSensor(entry)])


class ShiftSensor(SensorEntity):
    """De ploeg van vandaag: early, late of free. De week staat in de attributen."""

    _attr_has_entity_name = True
    _attr_translation_key = "shift"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(engine.SHIFTS)
    _attr_icon = "mdi:account-clock"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._mgr = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_shift"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return engine.shift_for(self._mgr.today(), self._mgr.shifts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snap_week = self._mgr.week()
        from datetime import timedelta

        nxt = engine.monday(self._mgr.today()) + timedelta(days=7)
        sh = self._mgr.shifts
        return {
            "week": snap_week,
            "this_week": engine.base_shift(engine.monday(self._mgr.today()), {**sh, "weekend_free": False}),
            "next_week": engine.base_shift(nxt, {**sh, "weekend_free": False}),
            "vacation": (sh.get("vacation") or {}).get("on", False),
            "rotation": sh.get("rotation"),
            "revision": self._mgr.revision,
        }
