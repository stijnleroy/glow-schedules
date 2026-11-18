# Glow Schedules

Schedules for Home Assistant that follow your shift work: **early shift**, **late shift** or **day off**. One schedule (e.g. heating) gets its own blocks per shift, and Glow Schedules picks the right one every day. Made for the [Glow Schedule Card](https://github.com/stijnleroy/glow-cards/blob/main/docs/schedule.md) from Glow Cards.

🇳🇱 [Nederlands](#nederlands)

## Features

- **Weekly rotation**: the shift alternates every Monday (late → early → late …). The weekend is a day off (optional).
- **Swap one day** (e.g. after a swap with a colleague), **swap the whole week**, or turn on **vacation**: every day then runs as a day off.
- **Schedules per shift or the same every day.** Times can follow sunrise or sunset (`sunset+30`).
- **What happens outside the blocks**: off, nothing, or a value (e.g. eco temperature 17 °C).
- **Values from helpers**: a block can take its temperature from an `input_number`, so changing the comfort temperature applies at once.
- **Restore after a restart**: after Home Assistant restarts, every schedule puts its devices in the right state.
- Runs entirely inside Home Assistant; no cloud.

Supported targets: `climate` (temperature), `light` (brightness), `cover` (open/close or position), `switch`, `fan`, `input_boolean` and anything that supports `turn_on`/`turn_off`, plus `scene` and `script` at the start of a block.

## Installation

1. HACS → ⋮ → *Custom repositories* → `stijnleroy/glow-schedules`, type *Integration*.
2. Download **Glow Schedules** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Glow Schedules*, and choose the shift you have this week.

## Entities

| Entity | Description |
|---|---|
| `sensor.glow_schedules_shift` | Today's shift (`early`, `late`, `free`), the week in the attributes. |
| `switch.glow_schedules_vacation` | Vacation: every day is a day off. |
| one `switch` per schedule | Schedule on/off, with today's blocks and the next change. |

## Services

| Service | |
|---|---|
| `glow_schedules.set_day_shift` | `date` (empty = today), `shift`: `early`, `late`, `free` or `auto` (back to the pattern). |
| `glow_schedules.swap_week` | Flip the pattern from this week on. |
| `glow_schedules.set_vacation` | `on`, optional `from` and `until`. |

Schedules are created and edited in the Glow Schedule Card (websocket API `glow_schedules/*`).

---

## Nederlands

Schema's voor Home Assistant die je ploegen volgen: **vroege ploeg**, **late ploeg** of **vrij**. Eén schema (bv. de verwarming) krijgt eigen blokken per ploeg, en Glow Schedules kiest elke dag de juiste.

- **Elke week om**: elke maandag wisselt de ploeg. Het weekend is vrij (instelbaar).
- **Eén dag wisselen** (bv. bij een ruil), **de hele week omwisselen** of **vakantie**: dan loopt elke dag als een vrije dag.
- **Per ploeg of elke dag hetzelfde**, ook met zonsopgang of zonsondergang (`sunset+30`).
- **Buiten de blokken**: uit, niets doen, of een waarde (bv. spaarstand 17 °C).
- **Waarden uit helpers**: de temperatuur van een blok kan uit een `input_number` komen. Pas je de comforttemperatuur aan, dan geldt die meteen.
- **Na een herstart** zet elk schema zijn toestellen meteen goed.
- Alles draait in Home Assistant zelf, zonder cloud.

**Installeren**: HACS → ⋮ → *Aangepaste repositories* → `stijnleroy/glow-schedules` (type *Integratie*) → downloaden → Home Assistant herstarten → *Instellingen → Apparaten & diensten → Integratie toevoegen → Glow Schedules*, en kies welke ploeg je deze week hebt.

Je maakt en bewerkt de schema's in de Glow Schedule Card.
