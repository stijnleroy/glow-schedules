"""Rekenwerk van Glow Schedules, los van Home Assistant zodat het testbaar is.

Begrippen
- ploeg (shift): "early" (vroeg), "late" (laat) of "free" (vrij).
- rotatie: de ploeg wisselt elke week volgens `rotation`, te tellen vanaf de maandag `anchor`.
- schema: blokken per ploeg (mode "shift") of één reeks blokken voor elke dag (mode "fixed").
- blok: {"from": "06:00", "to": "08:00", "value": 21}. Tijden mogen ook "sunset+30" of
  "sunrise-15" zijn (minuten t.o.v. zonsondergang/-opgang). "24:00" is middernacht op het einde.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any, Callable

SHIFTS = ("early", "late", "free")
DAY_MIN = 24 * 60

_SUN_RE = re.compile(r"^(sunrise|sunset)\s*(?:([+-])\s*(\d+))?$")
_HM_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")

SunFn = Callable[[str, date], "int | None"]  # (sunrise|sunset, dag) -> minuten na middernacht


# ---------------------------------------------------------------- ploegen


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def base_shift(d: date, shifts: dict[str, Any]) -> str:
    """Ploeg volgens het patroon, zonder vakantie of aanpassingen."""
    if shifts.get("weekend_free", True) and d.weekday() >= 5:
        return "free"
    rotation = [s for s in shifts.get("rotation") or ["late", "early"] if s in SHIFTS] or ["free"]
    anchor = date.fromisoformat(shifts.get("anchor") or monday(d).isoformat())
    weeks = (monday(d) - monday(anchor)).days // 7
    return rotation[weeks % len(rotation)]


def vacation_active(d: date, shifts: dict[str, Any]) -> bool:
    vac = shifts.get("vacation") or {}
    if not vac.get("on"):
        return False
    start = vac.get("from")
    until = vac.get("until")
    if start and d < date.fromisoformat(start):
        return False
    if until and d > date.fromisoformat(until):
        return False
    return True


def shift_for(d: date, shifts: dict[str, Any]) -> str:
    """De ploeg die op dag `d` geldt: vakantie > aanpassing van die dag > patroon."""
    if vacation_active(d, shifts):
        return "free"
    over = (shifts.get("overrides") or {}).get(d.isoformat())
    if over in SHIFTS:
        return over
    return base_shift(d, shifts)


def swap_week(shifts: dict[str, Any], today: date) -> dict[str, Any]:
    """Draai het patroon om vanaf deze week (de rotatie schuift één week op)."""
    out = dict(shifts)
    anchor = date.fromisoformat(shifts.get("anchor") or monday(today).isoformat())
    out["anchor"] = (monday(anchor) + timedelta(days=7)).isoformat()
    # aanpassingen van deze week en later vervallen: die hoorden bij het oude patroon
    start = monday(today)
    out["overrides"] = {k: v for k, v in (shifts.get("overrides") or {}).items() if date.fromisoformat(k) < start}
    return out


def set_day(shifts: dict[str, Any], d: date, shift: str | None) -> dict[str, Any]:
    """Zet de ploeg van één dag; None of gelijk aan het patroon wist de aanpassing."""
    out = dict(shifts)
    over = dict(shifts.get("overrides") or {})
    if shift not in SHIFTS or shift == base_shift(d, shifts):
        over.pop(d.isoformat(), None)
    else:
        over[d.isoformat()] = shift
    out["overrides"] = over
    return out


def prune_overrides(shifts: dict[str, Any], today: date, keep_days: int = 7) -> dict[str, Any]:
    """Oude aanpassingen opruimen zodat de opslag niet blijft groeien."""
    limit = today - timedelta(days=keep_days)
    over = {k: v for k, v in (shifts.get("overrides") or {}).items() if date.fromisoformat(k) >= limit}
    if over == (shifts.get("overrides") or {}):
        return shifts
    return {**shifts, "overrides": over}


# ---------------------------------------------------------------- tijden


def parse_time(value: Any, d: date, sun: SunFn | None, end: bool = False) -> int | None:
    """Tijd naar minuten na middernacht (0..1440). None als de tijd onbruikbaar is."""
    if value is None:
        return None
    s = str(value).strip().lower()
    m = _HM_RE.match(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h == 24 and mi == 0:
            return DAY_MIN
        if h > 23 or mi > 59:
            return None
        v = h * 60 + mi
        return DAY_MIN if end and v == 0 else v
    m = _SUN_RE.match(s)
    if m:
        if sun is None:
            return None
        base = sun(m.group(1), d)
        if base is None:
            return None
        off = int(m.group(3) or 0) * (-1 if m.group(2) == "-" else 1)
        return max(0, min(DAY_MIN, base + off))
    return None


def valid_time(value: Any) -> bool:
    s = str(value).strip().lower()
    if _SUN_RE.match(s):
        return True
    m = _HM_RE.match(s)
    return bool(m) and (int(m.group(1)) < 24 or (int(m.group(1)) == 24 and int(m.group(2)) == 0)) and int(m.group(2)) < 60


# ---------------------------------------------------------------- schema's


def variant_for(schedule: dict[str, Any], d: date, shifts: dict[str, Any]) -> str:
    if schedule.get("mode") == "shift":
        return shift_for(d, shifts)
    return "all"


def blocks_for(schedule: dict[str, Any], d: date, shifts: dict[str, Any], sun: SunFn | None) -> list[dict[str, Any]]:
    """De blokken van dag `d`, opgelost naar minuten en gesorteerd. Lege of foute blokken vallen weg."""
    variant = variant_for(schedule, d, shifts)
    raw = (schedule.get("blocks") or {}).get(variant) or []
    out = []
    for i, b in enumerate(raw):
        a = parse_time(b.get("from"), d, sun)
        e = parse_time(b.get("to"), d, sun, end=True)
        if a is None or e is None or e <= a:
            continue
        out.append({"index": i, "start": a, "end": e, "value": b.get("value")})
    out.sort(key=lambda x: x["start"])
    # overlappende blokken: het latere begint pas waar het vorige stopt
    clean: list[dict[str, Any]] = []
    for b in out:
        if clean and b["start"] < clean[-1]["end"]:
            b = {**b, "start": clean[-1]["end"]}
            if b["end"] <= b["start"]:
                continue
        clean.append(b)
    return clean


@dataclass
class Segment:
    """Wat een schema op een moment wil: in een blok (met waarde) of erbuiten."""

    key: str  # verandert telkens als er iets anders moet gebeuren
    inside: bool
    value: Any
    start: int  # minuten na middernacht
    end: int
    variant: str


def segment_at(schedule: dict[str, Any], now: datetime, shifts: dict[str, Any], sun: SunFn | None) -> Segment:
    d = now.date()
    minute = now.hour * 60 + now.minute
    variant = variant_for(schedule, d, shifts)
    blocks = blocks_for(schedule, d, shifts, sun)
    prev_end = 0
    for b in blocks:
        if b["start"] <= minute < b["end"]:
            value = b["value"] if b["value"] not in (None, "") else schedule.get("value")
            return Segment(f"{d}|in|{b['start']}|{b['end']}|{value}", True, value, b["start"], b["end"], variant)
        if minute < b["start"]:
            return Segment(f"{d}|out|{prev_end}|{b['start']}", False, None, prev_end, b["start"], variant)
        prev_end = b["end"]
    return Segment(f"{d}|out|{prev_end}|{DAY_MIN}", False, None, prev_end, DAY_MIN, variant)


def same_kind(a: Segment | None, b: Segment) -> bool:
    """Twee aansluitende stukken die hetzelfde vragen (bv. buiten de blokken over middernacht)."""
    if a is None:
        return False
    if a.inside != b.inside:
        return False
    return (not a.inside) or a.value == b.value


def next_change(schedule: dict[str, Any], now: datetime, shifts: dict[str, Any], sun: SunFn | None, days: int = 8) -> datetime | None:
    """Wanneer het schema de volgende keer iets doet (begin of einde van een blok)."""
    cur = segment_at(schedule, now, shifts, sun)
    t = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo) + timedelta(minutes=cur.end)
    for _ in range(days * 48):
        if t.date() > now.date() + timedelta(days=days):
            return None
        seg = segment_at(schedule, t, shifts, sun)
        if seg.inside != cur.inside or (seg.inside and seg.value != cur.value):
            return t
        cur = seg
        t = datetime.combine(t.date(), datetime.min.time(), tzinfo=now.tzinfo) + timedelta(minutes=seg.end)
    return None


# ---------------------------------------------------------------- acties


def _num(value: Any, read: Callable[[str], Any] | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and "." in value and not _is_float(value):
        if read is None:
            return None
        value = read(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def actions(schedule: dict[str, Any], seg: Segment, read: Callable[[str], Any] | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    """De service-aanroepen voor een stuk: binnen een blok "aan", erbuiten volgens `outside`.

    `read(entity_id)` geeft de toestand van een entiteit, voor waarden als input_number.comfort.
    """
    out: list[tuple[str, str, dict[str, Any]]] = []
    outside = schedule.get("outside", "off")
    if not seg.inside and outside == "none":
        return out
    for ent in schedule.get("targets") or []:
        dom = ent.split(".")[0]
        if seg.inside:
            v = _num(seg.value, read)
            if dom == "climate":
                if v is None:
                    out.append(("climate", "set_hvac_mode", {"entity_id": ent, "hvac_mode": "heat"}))
                else:
                    out.append(("climate", "set_temperature", {"entity_id": ent, "temperature": v, "hvac_mode": "heat"}))
            elif dom == "light":
                data: dict[str, Any] = {"entity_id": ent}
                if v is not None:
                    data["brightness_pct"] = max(1, min(100, round(v)))
                out.append(("light", "turn_on", data))
            elif dom == "cover":
                if v is None:
                    out.append(("cover", "open_cover", {"entity_id": ent}))
                else:
                    out.append(("cover", "set_cover_position", {"entity_id": ent, "position": max(0, min(100, round(v)))}))
            elif dom in ("scene", "script"):
                out.append((dom, "turn_on", {"entity_id": ent}))
            else:
                out.append(("homeassistant", "turn_on", {"entity_id": ent}))
        else:
            v = _num(schedule.get("outside_value"), read) if outside == "value" else None
            if dom == "climate":
                if v is not None:
                    out.append(("climate", "set_temperature", {"entity_id": ent, "temperature": v, "hvac_mode": "heat"}))
                else:
                    out.append(("climate", "set_hvac_mode", {"entity_id": ent, "hvac_mode": "off"}))
            elif dom == "light":
                if v is not None and v > 0:
                    out.append(("light", "turn_on", {"entity_id": ent, "brightness_pct": max(1, min(100, round(v)))}))
                else:
                    out.append(("light", "turn_off", {"entity_id": ent}))
            elif dom == "cover":
                if v is not None:
                    out.append(("cover", "set_cover_position", {"entity_id": ent, "position": max(0, min(100, round(v)))}))
                else:
                    out.append(("cover", "close_cover", {"entity_id": ent}))
            elif dom in ("scene", "script"):
                continue
            else:
                out.append(("homeassistant", "turn_off", {"entity_id": ent}))
    return out


# ---------------------------------------------------------------- controle


def clean_schedule(data: dict[str, Any]) -> dict[str, Any]:
    """Controleer en normaliseer een schema dat van buiten komt (kaart of service)."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    mode = data.get("mode", "fixed")
    if mode not in ("shift", "fixed"):
        raise ValueError("mode must be shift or fixed")
    targets = [str(t) for t in data.get("targets") or [] if "." in str(t)]
    if not targets:
        raise ValueError("at least one target entity is required")
    keys = SHIFTS if mode == "shift" else ("all",)
    src = data.get("blocks") or {}
    blocks: dict[str, list[dict[str, Any]]] = {}
    for k in keys:
        lst = []
        for b in src.get(k) or []:
            if not valid_time(b.get("from")) or not valid_time(b.get("to")):
                raise ValueError(f"invalid time in block {b}")
            item = {"from": str(b["from"]).strip().lower(), "to": str(b["to"]).strip().lower()}
            if b.get("value") not in (None, ""):
                item["value"] = b["value"]
            lst.append(item)
        blocks[k] = lst
    outside = data.get("outside", "off")
    if outside not in ("off", "none", "value"):
        raise ValueError("outside must be off, none or value")
    out = {
        "id": str(data.get("id") or ""),
        "name": name,
        "enabled": bool(data.get("enabled", True)),
        "mode": mode,
        "targets": targets,
        "blocks": blocks,
        "outside": outside,
    }
    if data.get("value") not in (None, ""):
        out["value"] = data["value"]
    if outside == "value" and data.get("outside_value") not in (None, ""):
        out["outside_value"] = data["outside_value"]
    if data.get("icon"):
        out["icon"] = str(data["icon"])
    return out


def clean_shifts(data: dict[str, Any]) -> dict[str, Any]:
    rotation = [s for s in data.get("rotation") or ["late", "early"] if s in SHIFTS]
    if not rotation:
        raise ValueError("rotation needs at least one shift")
    anchor = data.get("anchor")
    if anchor:
        date.fromisoformat(anchor)
    over = {}
    for k, v in (data.get("overrides") or {}).items():
        date.fromisoformat(k)
        if v in SHIFTS:
            over[k] = v
    vac = data.get("vacation") or {}
    vacation = {"on": bool(vac.get("on"))}
    for k in ("from", "until"):
        if vac.get(k):
            date.fromisoformat(vac[k])
            vacation[k] = vac[k]
    return {
        "rotation": rotation,
        "anchor": anchor,
        "weekend_free": bool(data.get("weekend_free", True)),
        "overrides": over,
        "vacation": vacation,
    }
