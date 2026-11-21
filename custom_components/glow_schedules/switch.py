"""Schakelaars: vakantie, en één per schema om het aan of uit te zetten."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_ADDED, SIGNAL_REMOVED, SIGNAL_UPDATE
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    mgr = entry.runtime_data
    async_add_entities([VacationSwitch(entry)] + [ScheduleSwitch(entry, sid) for sid in mgr.schedules])

    @callback
    def _added(sid: str) -> None:
        async_add_entities([ScheduleSwitch(entry, sid)])

    @callback
    def _removed(sid: str) -> None:
        reg = er.async_get(hass)
        eid = reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_schedule_{sid}")
        if eid:
            reg.async_remove(eid)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_ADDED, _added))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_REMOVED, _removed))


class _Base(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry) -> None:
        self._mgr = entry.runtime_data
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        if self.hass and self.entity_id:
            self.async_write_ha_state()


class VacationSwitch(_Base):
    """Vakantie: elke dag geldt als vrij tot je het uitzet."""

    _attr_translation_key = "vacation"
    _attr_icon = "mdi:palm-tree"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_vacation"

    @property
    def is_on(self) -> bool:
        return bool((self._mgr.shifts.get("vacation") or {}).get("on"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._mgr.async_set_vacation(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._mgr.async_set_vacation(False)


class ScheduleSwitch(_Base):
    """Eén schema aan of uit."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ConfigEntry, sid: str) -> None:
        super().__init__(entry)
        self._sid = sid
        self._attr_unique_id = f"{entry.entry_id}_schedule_{sid}"

    @property
    def _s(self) -> dict[str, Any] | None:
        return self._mgr.schedules.get(self._sid)

    @property
    def available(self) -> bool:
        return self._s is not None

    @property
    def name(self) -> str:
        s = self._s
        return s["name"] if s else self._sid

    @property
    def icon(self) -> str:
        s = self._s
        return (s or {}).get("icon") or "mdi:calendar-clock"

    @property
    def is_on(self) -> bool:
        s = self._s
        return bool(s and s.get("enabled", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._s
        if not s:
            return {}
        d = self._mgr.describe(s)
        today = next((x for x in d["week"] if x["date"] == self._mgr.today().isoformat()), None)
        return {
            "schedule_id": self._sid,
            "mode": s["mode"],
            "targets": s["targets"],
            "variant": d["now"]["variant"],
            "active": d["now"]["inside"] and s.get("enabled", True),
            "manual": d["manual"],
            "today": today["blocks"] if today else [],
            "next_change": d["next"],
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._mgr.async_set_enabled(self._sid, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._mgr.async_set_enabled(self._sid, False)
