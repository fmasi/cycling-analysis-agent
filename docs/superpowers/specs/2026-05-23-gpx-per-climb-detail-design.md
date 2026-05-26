# Per-climb detail in the GPX route-planning workflow

**Date:** 2026-05-23
**Status:** Design approved, pending implementation plan
**Branch context:** `feat/multi-bike-support`

## Problem

When a rider provides a **GPX route** for pre-ride planning, `analyse_gpx.py`
produces the text analysis, the whole-ride overview chart, and the lo-fi vs
hi-fi Fidelity Report — but **no per-climb zoom charts**. Those "nice graphics"
exist only in `analyse_climbs.py`, which:

1. runs on **FIT files** (recorded rides), not GPX routes, and
2. gates per-climb detail on **Cat 3+** (`index = length_km × avg_grade_pct ≥ 6`).

The index metric systematically under-weights **short, steep climbs**. A real
example: the Richmond Park loop's climb 2 is 580 m averaging 4.9% (index ≈ 2.8,
Cat 4) but contains a 63 m wall over 8% peaking at 10.6% (peak-25m). It never
triggered a detail chart, yet that gradient detail is exactly what is needed to
set precise power/cadence targets and keep a planned workout in-zone.

The rider wants, **at planning time (pre-ride), not just post-ride**:
- the text analysis *(exists)*
- the overall ride profile chart *(exists)*
- the per-climb zoom profile — the existing graphics *(missing on the GPX path)*
- the lo-fi vs hi-fi Fidelity Report *(exists)*

…and the per-climb detail must fire for short steep climbs, because workout
precision depends on accurate per-climb power and cadence numbers.

## Goals

- Bring per-climb zoom charts to the GPX planning workflow, reusing the
  existing `analyse_climbs` renderer (no new chart style).
- Trigger detail on a **significance gate** that catches short steep kicks, not
  just high-index climbs.
- Add **suggested gear + target cadence (rpm)** to each per-climb pacing row.
- Keep `analyse_climbs.py` (FIT path) output byte-identical.

## Non-goals (YAGNI)

- **Workout-zone-aware pacing** (pass `--zone sweetspot` / `--target-w 155`,
  get per-climb speed/rpm/gear to hold *that* power). Deferred. The cadence/gear
  helper built here is the shared core; the zone-aware mode later just feeds it
  a custom target wattage. Not built now.
- Any change to the Brompton/e-bike pacing model.
- New chart aesthetics — reuse the rider's existing `plot_climb_detail` look.

## Architecture (Approach B: shared modules)

Extract the renderer and categorisation out of `analyse_climbs.py` into shared
modules that **both** the FIT and GPX paths import. No backwards dependency
from the GPX route tool onto the FIT analysis tool.

| New module | Contents | Moved from |
|---|---|---|
| `scripts/chart_climb_detail.py` | `plot_climb_detail`, `resample_segment`, `grade_colour`, `climb_stats` | `analyse_climbs.py` |
| `scripts/climb_categories.py` | `CATEGORIES`, `categorise`, `is_significant()` | `analyse_climbs.py` (categorise/CATEGORIES) |
| `scripts/gearing.py` | `cadence_rpm()`, `suggest_gear()` | new |

`analyse_climbs.py` imports the moved functions back — its behaviour and output
stay identical (guarded by its existing golden tests). `analyse_gpx.py` imports
the same shared functions.

### Significance gate

`is_significant(climb, verification) -> (bool, reason)` returns `True` if **any**:

- **Cat 3+**: `index ≥ 6`, OR
- **wall present**: any entry in `verification.walls` (≥10% sustained ≥30m), OR
- **steep short pitch**: `verification.mean_max["peak_25m"] ≥ 8.0`.

`verification` is the per-climb `ClimbVerification` from the Fidelity Report.
When verification is unavailable (`--no-verify`), the gate falls back to GPX-only
signals: Cat 3+ by index, or GPX `max_grad_pct ≥ 8.0`.

The Richmond climb 2 qualifies via both wall and peak-25m. Climb 1 qualifies via
peak-25m (8.3%).

### Anti-spam cap

`--climb-detail-max N` (default **8**) bounds chart count on long/hilly routes.

- **Cat 3+ climbs are never capped** — every categorised climb renders.
- The cap applies **only to sub-Cat-3 climbs that qualified via the wall /
  peak-25m rule**. If more of those pass than the cap, render the **hardest N**
  (ranked by peak-25m, then index); list the remainder in the markdown as
  "also detected, chart omitted."
- Total charts = (all Cat 3+) + (top-N minor significant climbs).

### CLI

New on `analyse_gpx.py`:

- `--climb-detail auto` (default) — significance gate above.
- `--climb-detail all` — every detected climb (the "force it" switch for days
  when the rider knows precise detail is needed).
- `--climb-detail none` — overview + fidelity only, no per-climb charts.
- `--climb-detail 1,3` — explicit climb indices (1-based, matching the climbs
  table).
- `--climb-detail-max N` — cap for minor climbs (default 8).

### Bike-config gearing

Suggesting a gear needs the bike's gearing, which today lives only in
`USER_PROFILE.md` prose. Add to the `bikes.tripster` YAML:

```yaml
gearing:
  chainrings_t: [30, 39, 50]
  cassette_t: [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]
```

`BikeConfig` gains an optional `gearing` field (chainrings, cassette). Bikes
without it (Brompton) omit the gear/cadence columns gracefully.

### Cadence / gear helper (`scripts/gearing.py`)

- `cadence_rpm(speed_kmh, chainring_t, cog_t, wheel_circ_m) -> float` — pure
  maths: `rpm = (speed_m_min) / (wheel_circ_m × chainring_t / cog_t)`.
- `suggest_gear(speed_kmh, bike, prefer_rpm) -> (chainring_t, cog_t, rpm)` —
  picks the chainring×cog whose resulting cadence is closest to `prefer_rpm`,
  among realistic combos. Default `prefer_rpm` from rider profile: **70**
  (climbing comfort) for climbs, 85–90 flat.

This is the shared core the deferred workout-zone-aware mode (option 3) will
reuse: that mode computes speed from a target wattage on the real gradient, then
calls `suggest_gear`.

## Data flow in `analyse_gpx.py` (additive)

Existing steps unchanged:
1. parse GPX → detect climbs → predict pacing → `verify_route` (unless
   `--no-verify`) → Fidelity Report → overview chart.

New steps:
2. Build `arrays = {distance_m, altitude_m}` from `report.stitched_dists` /
   `report.stitched_elevs` (hi-fi) when verification ran; else from raw GPX
   elevation (lo-fi).
3. Select climbs (gate + cap, or `--climb-detail` override) → call
   `plot_climb_detail(arrays, climb, idx, out_path)` → save
   `rides/charts/<stem>-climbN.png`.
4. For each per-climb pacing row (FTP / MAP / Z3), add **gear** and **rpm**
   columns via `gearing.suggest_gear`.
5. Reference the new per-climb charts in the prediction markdown.

### Fidelity labelling / offline behaviour

Per-climb charts carry the same HI-FI / lo-fi badge as the overview. Under
`--no-verify` they render from GPX elevation, clearly marked lo-fi. The feature
degrades gracefully and never hard-fails the run.

### Output naming

`rides/charts/<stem>-climbN.png`, matching the `analyse_climbs` convention
(`<stem>-climb<N>.png`) and the existing `<stem>-overview.png`.

## Error handling

- No climbs detected → no per-climb charts; overview/fidelity/text as today.
- `resample_segment` returns < 4 points for a climb → skip that chart (existing
  `plot_climb_detail` guard), note in text.
- `verify_route` failed / `--no-verify` → lo-fi gate and lo-fi charts.
- Bike has no `gearing:` → omit gear/rpm columns, keep power/speed/VAM.

## Testing

- **Unit:** `is_significant` truth table including the Richmond climb-2 case
  (True via peak-25m and wall) and a trivial drag (False); cap logic asserting
  Cat 3+ is never dropped; `cadence_rpm` against hand-computed values;
  `suggest_gear` picks the expected combo for a known speed/prefer_rpm.
- **Regression:** run `analyse_gpx.py` on the Richmond GPX fixture → assert
  climb1 & climb2 PNGs exist, prediction MD references them, gear/rpm columns
  present.
- **Guard:** `analyse_climbs.py` (FIT) golden output unchanged after the
  extraction.

## Cleanup

Delete the throwaway `scripts/_per_climb_detail.py` once the feature lands.
