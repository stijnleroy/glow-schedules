"""Het hart van Glow Schedules: opslag, ploegen en het uitvoeren van de schema's."""
from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta
import logging
from typing import Any
import uuid

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Context, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from . import engine
from .const import SIGNAL_ADDED, SIGNAL_REMOVED, SIGNAL_UPDATE, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class ScheduleManager:
    """Houdt alle schema's en de ploeg bij en voert ze elke minuut uit."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.options = options
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.shifts: dict[str, Any] = {}
        self.schedules: dict[str, dict[str, Any]] = {}
        self.revision = 0
        self._applied: dict[str, engine.Segment] = {}
        self._unsubs: list = []
        self._value_unsub = None
        self._target_unsub = None
        self._ctx: deque[str] = deque(maxlen=64)  # contexten van onze eigen acties
        self.overrides: dict[str, str] = {}  # schema → stuk waarin iemand met de hand bijstuurde
        self._acted: dict[str, datetime] = {}  # wanneer wij een toestel het laatst bedienden
        self._sun_cache: dict[tuple[str, date], int | None] = {}

    # ------------------------------------------------------------ opslag

    async def async_load(self, initial_shifts: dict[str, Any]) -> None:
        data = await self._store.async_load() or {}
        self.shifts = engine.clean_shifts(data.get("shifts") or initial_shifts)
        self.schedules = {}
        for s in data.get("schedules") or []:
            try:
                c = engine.clean_schedule(s)
                self.schedules[c["id"]] = c
            except ValueError as err:
                _LOGGER.warning("Schema %s overgeslagen: %s", s.get("name"), err)

    def _data(self) -> dict[str, Any]:
        return {"shifts": self.shifts, "schedules": list(self.schedules.values())}

    def _changed(self) -> None:
        self.revision += 1
        self._store.async_delay_save(self._data, 1)
        self._track_values()
        self._track_targets()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _bump(self) -> None:
        """Iets veranderd dat niet bewaard moet worden (bv. met de hand bijgestuurd)."""
        self.revision += 1
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ------------------------------------------------------------ starten en stoppen

    async def async_start(self) -> None:
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))
        self._track_values()
        self._track_targets()

        async def _started(_: Event | None = None) -> None:
            # na een herstart de juiste toestand meteen herstellen
            await self.async_evaluate(force=self.options.get("restore", True))

        if self.hass.state is CoreState.running:
            self.hass.async_create_task(_started())
        else:
            self._unsubs.append(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started))

    async def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        if self._value_unsub:
            self._value_unsub()
            self._value_unsub = None
        if self._target_unsub:
            self._target_unsub()
            self._target_unsub = None
        await self._store.async_save(self._data())

    # ------------------------------------------------------------ hulpjes

    def sun(self, kind: str, day: date) -> int | None:
        key = (kind, day)
        if key not in self._sun_cache:
            ev = get_astral_event_date(self.hass, kind, day)
            if ev is None:
                self._sun_cache[key] = None
            else:
                loc = dt_util.as_local(ev)
                self._sun_cache[key] = loc.hour * 60 + loc.minute
            if len(self._sun_cache) > 64:
                self._sun_cache = {key: self._sun_cache[key]}
        return self._sun_cache[key]

    def read(self, entity_id: str) -> Any:
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ("unknown", "unavailable"):
            return None
        return st.state

    def today(self) -> date:
        return dt_util.now().date()

    # ------------------------------------------------------------ uitvoeren

    @callback
    def _tick(self, now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate())

    async def async_evaluate(self, force: bool = False, only: str | None = None) -> None:
        now = dt_util.now()
        if now.hour == 0 and now.minute == 0:
            pruned = engine.prune_overrides(self.shifts, now.date())
            if pruned is not self.shifts:
                self.shifts = pruned
                self._changed()
        for sid, s in list(self.schedules.items()):
            if only and sid != only:
                continue
            if not s.get("enabled", True):
                self._applied.pop(sid, None)
                continue
            seg = engine.segment_at(s, now, self.shifts, self.sun)
            prev = self._applied.get(sid)
            if not force and prev is not None and (prev.key == seg.key or engine.same_kind(prev, seg)):
                self._applied[sid] = seg
                continue
            if prev is None and not force:
                # eerste keer na laden zonder herstel: alleen onthouden
                self._applied[sid] = seg
                continue
            self._applied[sid] = seg
            if self.overrides.pop(sid, None) is not None:
                self.revision += 1
            await self._run(s, seg)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def _run(self, s: dict[str, Any], seg: engine.Segment) -> None:
        for domain, service, data in engine.actions(s, seg, self.read):
            ent = data.get("entity_id")
            if ent and self.hass.states.get(ent) is None:
                _LOGGER.warning("Schema %s: %s bestaat niet", s["name"], ent)
                continue
            ctx = Context()
            self._ctx.append(ctx.id)
            if ent:
                self._acted[ent] = dt_util.utcnow()
            try:
                await self.hass.services.async_call(domain, service, data, blocking=True, context=ctx)
            except Exception as err:  # noqa: BLE001 - één fout mag de rest niet tegenhouden
                _LOGGER.error("Schema %s: %s.%s mislukt: %s", s["name"], domain, service, err)

    def _track_values(self) -> None:
        """Verandert een waarde-entiteit (bv. comfort-temperatuur), dan het lopende blok opnieuw toepassen."""
        ents: dict[str, list[str]] = {}
        for sid, s in self.schedules.items():
            vals = [s.get("value"), s.get("outside_value")]
            vals += [b.get("value") for lst in (s.get("blocks") or {}).values() for b in lst]
            for v in vals:
                if isinstance(v, str) and "." in v and not engine._is_float(v):
                    ents.setdefault(v, []).append(sid)
        if self._value_unsub:
            self._value_unsub()
            self._value_unsub = None
        if not ents:
            return

        @callback
        def _changed(event: Event) -> None:
            for sid in ents.get(event.data["entity_id"], []):
                if sid in self.overrides:
                    continue
                self.hass.async_create_task(self.async_evaluate(force=True, only=sid))

        self._value_unsub = async_track_state_change_event(self.hass, list(ents), _changed)

    def _track_targets(self) -> None:
        """Stuurt iemand een toestel met de hand bij, dan blijft dat zo tot het volgende blok."""
        ents: dict[str, list[str]] = {}
        for sid, s in self.schedules.items():
            for t in s.get("targets") or []:
                ents.setdefault(t, []).append(sid)
        if self._target_unsub:
            self._target_unsub()
            self._target_unsub = None
        if not ents:
            return

        @callback
        def _changed(event: Event) -> None:
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if old is None or new is None or new.state in ("unavailable", "unknown") or old.state in ("unavailable", "unknown"):
                return
            ctx = event.context
            if ctx.id in self._ctx or (ctx.parent_id and ctx.parent_id in self._ctx):
                return
            # cloudtoestellen (bv. Nest) melden onze eigen opdracht soms later en zonder context
            acted = self._acted.get(event.data["entity_id"])
            if acted and dt_util.utcnow() - acted < timedelta(seconds=120):
                return
            # alleen echte bediening telt, geen gemeten waarden
            keys = ("temperature", "target_temp_low", "target_temp_high", "brightness", "current_position")
            if old.state == new.state and all(old.attributes.get(k) == new.attributes.get(k) for k in keys):
                return
            changed = False
            for sid in ents.get(event.data["entity_id"], []):
                s = self.schedules.get(sid)
                seg = self._applied.get(sid)
                if not s or not s.get("enabled", True) or seg is None or self.overrides.get(sid) == seg.key:
                    continue
                self.overrides[sid] = seg.key
                changed = True
            if changed:
                self._bump()

        self._target_unsub = async_track_state_change_event(self.hass, list(ents), _changed)

    async def async_apply(self, sid: str) -> None:
        """Terug naar het schema: het huidige blok (of de stand erbuiten) meteen toepassen."""
        if sid not in self.schedules:
            raise KeyError(sid)
        self.overrides.pop(sid, None)
        self._bump()
        await self.async_evaluate(only=sid, force=True)

    # ------------------------------------------------------------ schema's beheren

    async def async_save_schedule(self, data: dict[str, Any]) -> dict[str, Any]:
        sid = data.get("id") or uuid.uuid4().hex[:8]
        new = sid not in self.schedules
        clean = engine.clean_schedule({**data, "id": sid})
        self.schedules[sid] = clean
        self._changed()
        if new:
            async_dispatcher_send(self.hass, SIGNAL_ADDED, sid)
        # een aangepast schema meteen toepassen als het lopende stuk veranderde
        await self.async_evaluate(only=sid, force=new and clean["enabled"])
        return clean

    async def async_delete_schedule(self, sid: str) -> None:
        if self.schedules.pop(sid, None) is not None:
            self._applied.pop(sid, None)
            self._changed()
            async_dispatcher_send(self.hass, SIGNAL_REMOVED, sid)

    async def async_set_enabled(self, sid: str, enabled: bool) -> None:
        s = self.schedules.get(sid)
        if s is None:
            raise KeyError(sid)
        s["enabled"] = bool(enabled)
        self.overrides.pop(sid, None)
        self._applied.pop(sid, None)
        self._changed()
        if enabled:
            await self.async_evaluate(only=sid, force=True)

    # ------------------------------------------------------------ ploegen

    async def async_update_shifts(self, shifts: dict[str, Any]) -> None:
        self.shifts = engine.clean_shifts(shifts)
        self._changed()
        await self.async_evaluate()

    async def async_set_day(self, day: date, shift: str | None) -> None:
        await self.async_update_shifts(engine.set_day(self.shifts, day, shift))

    async def async_swap_week(self) -> None:
        await self.async_update_shifts(engine.swap_week(self.shifts, self.today()))

    async def async_set_vacation(self, on: bool, start: str | None = None, until: str | None = None) -> None:
        vac: dict[str, Any] = {"on": bool(on)}
        if on and start:
            vac["from"] = start
        if on and until:
            vac["until"] = until
        await self.async_update_shifts({**self.shifts, "vacation": vac})

    # ------------------------------------------------------------ voor de kaart

    def week(self, start: date | None = None) -> list[dict[str, Any]]:
        start = start or engine.monday(self.today())
        out = []
        for i in range(7):
            d = start + timedelta(days=i)
            out.append({
                "date": d.isoformat(),
                "shift": engine.shift_for(d, self.shifts),
                "base": engine.base_shift(d, self.shifts),
                "override": (self.shifts.get("overrides") or {}).get(d.isoformat()),
                "vacation": engine.vacation_active(d, self.shifts),
            })
        return out

    def describe(self, s: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Een schema met zijn opgeloste blokken voor deze week en wat er nu gebeurt."""
        now = now or dt_util.now()
        start = engine.monday(now.date())
        days = []
        for i in range(7):
            d = start + timedelta(days=i)
            days.append({
                "date": d.isoformat(),
                "variant": engine.variant_for(s, d, self.shifts),
                "blocks": [[b["start"], b["end"], b["value"] if b["value"] not in (None, "") else s.get("value")] for b in engine.blocks_for(s, d, self.shifts, self.sun)],
            })
        seg = engine.segment_at(s, now, self.shifts, self.sun)
        nxt = engine.next_change(s, now, self.shifts, self.sun) if s.get("enabled", True) else None
        return {
            **s,
            "week": days,
            "now": {"inside": seg.inside, "value": seg.value, "start": seg.start, "end": seg.end, "variant": seg.variant},
            "manual": s["id"] in self.overrides,
            "next": nxt.isoformat() if nxt else None,
        }

    def snapshot(self) -> dict[str, Any]:
        now = dt_util.now()
        today = now.date()
        nxt = engine.monday(today) + timedelta(days=7)
        return {
            "revision": self.revision,
            "today": today.isoformat(),
            "minute": now.hour * 60 + now.minute,
            "shifts": self.shifts,
            "week": self.week(),
            "next_week": engine.shift_for(nxt, {**self.shifts, "overrides": {}, "vacation": {"on": False}}),
            "sun": {"sunrise": self.sun("sunrise", today), "sunset": self.sun("sunset", today)},
            "schedules": [self.describe(s, now) for s in self.schedules.values()],
        }
