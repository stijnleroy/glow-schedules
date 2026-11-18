from datetime import date, datetime, timedelta
import importlib.util
import pathlib

import pytest

_p = pathlib.Path(__file__).parent.parent / "custom_components" / "glow_schedules" / "engine.py"
_spec = importlib.util.spec_from_file_location("engine", _p)
E = importlib.util.module_from_spec(_spec)
import sys
sys.modules["engine"] = E
_spec.loader.exec_module(E)

SH = {"rotation": ["late", "early"], "anchor": "2026-11-16", "weekend_free": True, "overrides": {}, "vacation": {"on": False}}
SUN = lambda kind, d: 16 * 60 + 54 if kind == "sunset" else 8 * 60  # noqa: E731

HEAT = {
    "id": "h", "name": "Verwarming", "enabled": True, "mode": "shift", "targets": ["climate.living"],
    "value": "input_number.comfort", "outside": "value", "outside_value": "input_number.eco",
    "blocks": {
        "early": [{"from": "13:15", "to": "21:00"}],
        "late": [{"from": "06:00", "to": "08:00"}, {"from": "15:30", "to": "22:00", "value": 21.5}],
        "free": [{"from": "08:00", "to": "11:00"}, {"from": "16:30", "to": "22:00"}],
    },
}
XMAS = {"id": "x", "name": "Kerst", "enabled": True, "mode": "fixed", "targets": ["switch.kerst"], "outside": "off",
        "blocks": {"all": [{"from": "00:00", "to": "00:30"}, {"from": "sunset", "to": "24:00"}]}}


def dt(s):
    return datetime.fromisoformat(s)


def test_rotation():
    assert E.shift_for(date(2026, 11, 18), SH) == "late"
    assert E.shift_for(date(2026, 11, 23), SH) == "early"
    assert E.shift_for(date(2026, 11, 30), SH) == "late"
    assert E.shift_for(date(2026, 11, 9), SH) == "early"
    assert E.shift_for(date(2026, 11, 21), SH) == "free"  # zaterdag


def test_override_and_vacation():
    s = E.set_day(SH, date(2026, 11, 19), "early")
    assert E.shift_for(date(2026, 11, 19), s) == "early"
    assert E.set_day(s, date(2026, 11, 19), "late")["overrides"] == {}  # terug naar patroon wist
    v = {**SH, "vacation": {"on": True, "until": "2026-11-20"}}
    assert E.shift_for(date(2026, 11, 19), v) == "free"
    assert E.shift_for(date(2026, 11, 23), v) == "early"


def test_swap_week():
    s = E.swap_week(E.set_day(SH, date(2026, 11, 19), "free"), date(2026, 11, 18))
    assert E.shift_for(date(2026, 11, 18), s) == "early"
    assert E.shift_for(date(2026, 11, 23), s) == "late"
    assert s["overrides"] == {}


def test_segments_heat():
    seg = E.segment_at(HEAT, dt("2026-11-18T16:44"), SH, SUN)
    assert seg.inside and seg.value == 21.5 and seg.variant == "late"
    seg = E.segment_at(HEAT, dt("2026-11-18T07:00"), SH, SUN)
    assert seg.inside and seg.value == "input_number.comfort"
    seg = E.segment_at(HEAT, dt("2026-11-18T12:00"), SH, SUN)
    assert not seg.inside and (seg.start, seg.end) == (480, 930)
    seg = E.segment_at(HEAT, dt("2026-11-23T14:00"), SH, SUN)
    assert seg.inside and seg.variant == "early"


def test_sun_and_midnight():
    seg = E.segment_at(XMAS, dt("2026-11-18T17:00"), SH, SUN)
    assert seg.inside and seg.start == 1014 and seg.end == 1440
    seg = E.segment_at(XMAS, dt("2026-11-18T00:10"), SH, SUN)
    assert seg.inside
    assert not E.segment_at(XMAS, dt("2026-11-18T10:00"), SH, SUN).inside
    # 23:59 → 00:00 blijft "aan": geen nieuwe actie nodig
    a = E.segment_at(XMAS, dt("2026-11-18T23:59"), SH, SUN)
    b = E.segment_at(XMAS, dt("2026-11-19T00:00"), SH, SUN)
    assert a.key != b.key and E.same_kind(a, b)


def test_next_change():
    n = E.next_change(XMAS, dt("2026-11-18T10:00"), SH, SUN)
    assert n == dt("2026-11-18T16:54")
    n = E.next_change(XMAS, dt("2026-11-18T20:00"), SH, SUN)
    assert n == dt("2026-11-19T00:30")
    n = E.next_change(HEAT, dt("2026-11-20T23:00"), SH, SUN)  # vrijdag laat → zaterdag vrij 08:00
    assert n == dt("2026-11-21T08:00")


def test_actions():
    read = {"input_number.comfort": "21", "input_number.eco": "17"}.get
    seg = E.segment_at(HEAT, dt("2026-11-18T07:00"), SH, SUN)
    assert E.actions(HEAT, seg, read) == [("climate", "set_temperature", {"entity_id": "climate.living", "temperature": 21.0, "hvac_mode": "heat"})]
    seg = E.segment_at(HEAT, dt("2026-11-18T12:00"), SH, SUN)
    assert E.actions(HEAT, seg, read)[0][2]["temperature"] == 17.0
    off = {**HEAT, "outside": "off"}
    assert E.actions(off, seg, read) == [("climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "off"})]
    assert E.actions({**HEAT, "outside": "none"}, seg, read) == []
    seg = E.segment_at(XMAS, dt("2026-11-18T12:00"), SH, SUN)
    assert E.actions(XMAS, seg) == [("homeassistant", "turn_off", {"entity_id": "switch.kerst"})]


def test_overlap_and_bad_blocks():
    s = {**XMAS, "blocks": {"all": [{"from": "10:00", "to": "12:00"}, {"from": "11:00", "to": "13:00"}, {"from": "15:00", "to": "14:00"}]}}
    bl = E.blocks_for(s, date(2026, 11, 18), SH, SUN)
    assert [(b["start"], b["end"]) for b in bl] == [(600, 720), (720, 780)]


def test_clean():
    c = E.clean_schedule({**HEAT, "id": "h"})
    assert c["blocks"]["late"][1]["value"] == 21.5
    with pytest.raises(ValueError):
        E.clean_schedule({**XMAS, "blocks": {"all": [{"from": "25:00", "to": "26:00"}]}})
    with pytest.raises(ValueError):
        E.clean_schedule({**XMAS, "targets": []})
    assert E.clean_shifts(SH)["rotation"] == ["late", "early"]
