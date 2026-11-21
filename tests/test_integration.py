from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

TZ = ZoneInfo("Europe/Brussels")
HEAT = {
    "name": "Verwarming", "mode": "shift", "targets": ["climate.living"],
    "value": "input_number.comfort", "outside": "value", "outside_value": "input_number.eco",
    "blocks": {
        "early": [{"from": "13:15", "to": "21:00"}],
        "late": [{"from": "06:00", "to": "08:00"}, {"from": "15:30", "to": "22:00"}],
        "free": [{"from": "08:00", "to": "11:00"}, {"from": "16:30", "to": "22:00"}],
    },
}


async def _setup(hass, freezer, hass_ws_client):
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.config.latitude, hass.config.longitude = 51.0, 3.88
    assert await async_setup_component(hass, "websocket_api", {})
    ws = await hass_ws_client(hass)
    freezer.move_to(datetime(2026, 11, 18, 12, 0, tzinfo=TZ))
    hass.states.async_set("climate.living", "heat")
    hass.states.async_set("input_number.comfort", "21")
    hass.states.async_set("input_number.eco", "17")
    hass.states.async_set("switch.kerst", "off")
    calls = async_mock_service(hass, "climate", "set_temperature")
    on = async_mock_service(hass, "homeassistant", "turn_on")
    off = async_mock_service(hass, "homeassistant", "turn_off")
    entry = MockConfigEntry(domain="glow_schedules", data={"this_week": "late", "weekend_free": True, "restore": True}, unique_id="glow_schedules")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls, on, off, ws


async def test_flow_shift_and_heating(hass: HomeAssistant, hass_ws_client, freezer):
    entry, calls, on, off, ws = await _setup(hass, freezer, hass_ws_client)
    assert hass.states.get("sensor.glow_schedules_ploeg") or hass.states.get("sensor.glow_schedules_shift")

    await ws.send_json({"id": 1, "type": "glow_schedules/save", "schedule": HEAT})
    res = await ws.receive_json()
    assert res["success"], res
    sid = res["result"]["id"]
    await hass.async_block_till_done()
    # nieuw schema: meteen buiten de blokken → spaarstand
    assert calls[-1].data["temperature"] == 17.0

    freezer.move_to(datetime(2026, 11, 18, 15, 30, tzinfo=TZ))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 21.0
    n = len(calls)

    # een minuut later: niets nieuws
    freezer.move_to(datetime(2026, 11, 18, 15, 31, tzinfo=TZ))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == n

    # comfort-temperatuur aangepast tijdens een blok → meteen toegepast
    hass.states.async_set("input_number.comfort", "21.5")
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 21.5

    # vandaag vrij → om 15:31 buiten de blokken van "vrij"
    await ws.send_json({"id": 2, "type": "glow_schedules/shift", "action": "set_day", "shift": "free"})
    res = await ws.receive_json()
    assert res["success"], res
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 17.0
    assert res["result"]["week"][2]["shift"] == "free"
    assert hass.states.get("sensor.glow_schedules_shift").state == "free"

    # schakelaar van het schema
    sw = [s for s in hass.states.async_all("switch") if s.attributes.get("schedule_id") == sid]
    assert sw and sw[0].state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw[0].entity_id}, blocking=True)
    assert hass.states.get(sw[0].entity_id).state == "off"

    # vakantie via de schakelaar
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.glow_schedules_vacation"}, blocking=True)
    await ws.send_json({"id": 3, "type": "glow_schedules/get"})
    res = await ws.receive_json()
    assert all(d["shift"] == "free" for d in res["result"]["week"])
    assert res["result"]["next_week"] == "early"

    # weekwissel en dienst
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.glow_schedules_vacation"}, blocking=True)
    await hass.services.async_call("glow_schedules", "swap_week", {}, blocking=True)
    assert hass.states.get("sensor.glow_schedules_shift").attributes["this_week"] == "early"


async def test_fixed_switch_and_delete(hass: HomeAssistant, hass_ws_client, freezer):
    entry, calls, on, off, ws = await _setup(hass, freezer, hass_ws_client)
    await ws.send_json({"id": 1, "type": "glow_schedules/save", "schedule": {"name": "Kerst", "mode": "fixed", "targets": ["switch.kerst"], "outside": "off", "blocks": {"all": [{"from": "sunset", "to": "24:00"}]}}})
    res = await ws.receive_json()
    assert res["success"], res
    sid = res["result"]["id"]
    await hass.async_block_till_done()
    assert off and off[-1].data["entity_id"] in ("switch.kerst", ["switch.kerst"])
    # zonsondergang opgelost in de weekdata
    blocks = res["result"]["week"][2]["blocks"]
    assert blocks and 900 < blocks[0][0] < 1100
    await ws.send_json({"id": 2, "type": "glow_schedules/delete", "schedule_id": sid})
    res = await ws.receive_json()
    await hass.async_block_till_done()
    assert not [s for s in hass.states.async_all("switch") if s.attributes.get("schedule_id") == sid]


async def test_restart_keeps_data(hass: HomeAssistant, hass_ws_client, freezer):
    entry, calls, on, off, ws = await _setup(hass, freezer, hass_ws_client)
    await ws.send_json({"id": 1, "type": "glow_schedules/save", "schedule": HEAT})
    await ws.receive_json()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await ws.send_json({"id": 2, "type": "glow_schedules/get"})
    res = await ws.receive_json()
    assert [s["name"] for s in res["result"]["schedules"]] == ["Verwarming"]


async def test_config_flow(hass: HomeAssistant):
    from homeassistant import config_entries

    r = await hass.config_entries.flow.async_init("glow_schedules", context={"source": config_entries.SOURCE_USER})
    assert r["type"] == "form"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"this_week": "late", "weekend_free": True, "restore": True})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.states.get("sensor.glow_schedules_shift") is not None
    r = await hass.config_entries.flow.async_init("glow_schedules", context={"source": config_entries.SOURCE_USER})
    assert r["type"] == "abort"


async def test_manual_override_until_next_block(hass: HomeAssistant, hass_ws_client, freezer):
    entry, calls, on, off, ws = await _setup(hass, freezer, hass_ws_client)
    await ws.send_json({"id": 1, "type": "glow_schedules/save", "schedule": HEAT})
    res = await ws.receive_json()
    sid = res["result"]["id"]
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 17.0
    n = len(calls)

    # iemand zet de thermostaat met de hand hoger (na de wachttijd van 2 minuten)
    freezer.move_to(datetime(2026, 11, 18, 12, 5, tzinfo=TZ))
    hass.states.async_set("climate.living", "heat", {"temperature": 22})
    await hass.async_block_till_done()
    await ws.send_json({"id": 2, "type": "glow_schedules/get"})
    res = await ws.receive_json()
    assert res["result"]["schedules"][0]["manual"] is True

    # een waarde-entiteit verandert: tijdens het bijsturen niet opnieuw toepassen
    hass.states.async_set("input_number.eco", "16")
    await hass.async_block_till_done()
    assert len(calls) == n

    # volgend blok: schema neemt het weer over
    freezer.move_to(datetime(2026, 11, 18, 15, 30, tzinfo=TZ))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 21.0
    await ws.send_json({"id": 3, "type": "glow_schedules/get"})
    res = await ws.receive_json()
    assert res["result"]["schedules"][0]["manual"] is False

    # opnieuw bijsturen en dan "terug naar schema"
    freezer.move_to(datetime(2026, 11, 18, 16, 0, tzinfo=TZ))
    hass.states.async_set("climate.living", "off", {"temperature": 22})
    await hass.async_block_till_done()
    await ws.send_json({"id": 4, "type": "glow_schedules/apply", "schedule_id": sid})
    res = await ws.receive_json()
    assert res["success"], res
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 21.0
