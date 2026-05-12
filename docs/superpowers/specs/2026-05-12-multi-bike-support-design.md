# Multi-Bike Support — Design Spec

**Date:** 2026-05-12
**Status:** Draft for review

## Problem

The framework today assumes a single bike. `USER_PROFILE.md` has a top-level `physics:` block hardcoding the Kinesis Tripster's weight, CdA, F/R split, drivetrain efficiency. `scripts/physics_model.py` hardcodes wheel circumference (2.155 m, 700c × 32), CRR (TPU road defaults), and other tyre-specific constants. Every analysis script reads this single block.

The rider also uses a Brompton G Line Electric — a different bike on every axis that matters: 20" wheels, gravel tyres, e-assist, no power meter, different position, capable of off-road surfaces. Analysing a Brompton ride with the existing scripts produces silently wrong results:

- Speed-from-RPM off by ~35% (1.59 m vs 2.155 m wheel circumference)
- Power-physics under-estimates effort (11.6 kg vs 19.5 kg bike weight)
- TSS calculation expects power data that doesn't exist
- No model of motor contribution to wheel power
- Tyre pressure script outputs road numbers for a 54-406 gravel tyre

The framework needs to support multiple bikes with per-bike physics, surface-aware CRR, e-assist modelling, and an HR-primary output shape for the Brompton.

## Goal

A multi-bike data model in `USER_PROFILE.md`, a uniform `--bike` CLI contract across all physics-touching scripts, agent rules in `CLAUDE.md` that select the right bike from context, and physics-model changes that handle e-assist and surface variation.

The acceptance test is running tomorrow's Long Gravel route (`routes/2026-05-12_2950040052_Long Gravel ride (To be Tested and verified).gpx`) through the updated `analyse_gpx.py` with `--bike brompton-g --surface gravel` and getting a usable HR-zone pacing plan with battery-drain estimate.

## Non-goals

- Adding a power meter to the Brompton. Researched and ruled out (cost, fold-mechanism trade-off).
- Inferring rider crank wattage from HR for Brompton predictions. Too fragile; HR-primary outputs only.
- Retrofitting existing Tripster ride analyses to the new schema.
- General N-bike support beyond the current two. Schema is extensible but only validated for Tripster + Brompton G Line.
- Per-tyre CdA modelling. CdA stays per-bike, not per-tyre.

## Decisions made during brainstorming

1. **Scope**: Full treatment — schema change, per-ride bike selection in the framework, then route analysis. Not a one-off override.
2. **Data model**: `bikes:` dict keyed by slug + `default_bike:` pointer. The top-level `physics:` block is removed (after a temporary alias period during migration).
3. **Bike selection**: Agent asks only when ambiguous; scripts default to `default_bike` with a stderr warning.
4. **Brompton model**: HR-primary outputs (no rider wattage quoted), physics still runs internally for speed and battery estimates.
5. **Power meter**: Skip entirely. hrTSS is acceptable for Brompton's role as commute + top-up volume.

## Architecture

### `USER_PROFILE.md` schema

```yaml
default_bike: tripster

bikes:
  tripster:
    name: Kinesis Decade Tripster
    bike_weight_kg: 11.6
    system_weight_kg_default: 90.1     # body + bike + kit baseline
    fr_split: "40/60"
    cda: 0.28
    cda_range: "0.26–0.30 (hoods, upright endurance)"
    drivetrain_efficiency: 0.97
    wheel_circ_m: 2.155                # 700c × 32 mm
    has_power_meter: true              # Stages left-crank, FC-5700
    tyres:
      model: Continental GP 4 Seasons
      size_mm: 32
      measured_mm: 31.4
    crr:
      tpu_optimal: 0.0050
      tpu_intermediate: 0.0055
      tpu_high_or_butyl: 0.0058
    crr_by_surface:
      tarmac: 0.0050                   # alias to tpu_optimal at baseline
    surfaces_supported: [tarmac]

  brompton_g:
    name: Brompton G Line Electric
    bike_weight_kg: 19.5                # with battery
    bike_weight_kg_no_battery: 15.7
    fr_split: "TBD"                     # pending bathroom-scale measurement
    cda: 0.42                           # placeholder; review-position estimate
    cda_range: "0.40–0.45 (less upright than classic Brompton; full-size feel)"
    drivetrain_efficiency: 0.96         # 4-spd derailleur via rear-hub motor freehub
    wheel_circ_m: 1.59                  # 20" / 406 mm
    has_power_meter: false
    tyres:
      model: Schwalbe G-One Allround
      size_etrto: "54-406"              # ≈ 2.1" gravel
    crr_by_surface:
      tarmac: 0.0100
      tarmac_high_pressure: 0.0120
      gravel_smooth: 0.0180
      gravel_rough: 0.0250
    surfaces_supported: [tarmac, gravel]
    assist:
      type: e-Motiq
      placement: rear_hub
      rated_w: 250
      peak_w: 450                        # Boost only, short bursts
      torque_nm: 30
      sensor: torque
      cutoff_kph: 25                     # EU/UK pedelec
      levels: [L0, L1, L2, L3]
      boost_mode: true
      battery_wh: 345
      battery_range_km: "30–60"          # manufacturer claim
      level_share:                       # placeholder multipliers; calibrate from rides
        L0: 0.0
        L1: 0.5
        L2: 1.0
        L3: 1.5
      default_level_flat: L1
      default_level_climb_5pct: L2
      default_level_climb_10pct: L3
```

Field notes:
- `system_weight_kg_default` is per-bike because kit + carry weight differs (commute backpack ≠ road kit).
- `surfaces_supported` clamps `--surface` to valid options for the bike.
- `crr_by_surface` is the new authoritative CRR source; legacy `crr.tpu_*` keys remain on the Tripster for documentation continuity.
- `assist` block only exists on motorised bikes. Scripts check `assist in bike` before applying motor logic.
- `level_share` values are explicit placeholders flagged for calibration (see Pending experiments).

### Script CLI contract

All physics-touching scripts (`analyse_fit.py`, `analyse_gpx.py`, `analyse_climbs.py`, `physics_model.py`, `tyre_pressure.py`, `training_load.py`) accept:

```
--bike <slug>            # optional; slug must exist in bikes: dict
--surface <name>         # optional; defaults to bike's first surfaces_supported
--assist-level L0|L1|L2|L3   # only meaningful for bikes with assist; ignored otherwise
```

Resolution order:

1. `--bike` passed and matches → use it.
2. `--bike` passed and does not match → **hard fail** listing valid slugs.
3. `--bike` omitted → use `default_bike:`, emit a one-line stderr warning: `using default bike 'tripster' (no --bike specified)`.

A new module `scripts/profile_loader.py` exposes:

```python
def load_bike(profile_path: Path, slug: str | None) -> BikeConfig:
    """Returns a BikeConfig dataclass; raises with valid-slug list on bad slug."""
```

Every script that previously read `profile["physics"]["bike_weight_kg"]` migrates to `bike = load_bike(profile, args.bike)` then `bike.bike_weight_kg`. Single point of change.

Every saved analysis markdown leads with a header line: `Bike: <name> (<slug>)`. Surface, and assist level where relevant, follow on the same line.

### Agent rules in `CLAUDE.md`

A new top-level section between **"How this framework works"** and **"Core principles"**:

> **Bike selection** — every FIT analysis and GPX prediction is bike-specific. Determine the bike before running scripts.
>
> **Primary signal (high confidence):**
> - FIT contains power records → **Tripster**
> - FIT has no power records → **Brompton G Line**
>
> **Secondary signals:**
> - Rider mentions the bike in the current message
> - GPX waypoint or filename contains "commute"
> - Recent ride log entry on the same day already names the bike
>
> **Rare exceptions:** Tripster ride with dead power-meter battery looks Brompton-like. Confirm via distance, avg speed, or asking.
>
> **Ambiguous → ask the rider** with a concrete recommendation based on weak signals ("looks like the Tripster based on distance — confirm?") rather than an open question.
>
> Every saved analysis records the bike slug in its header. The ride log in `USER_PROFILE.md` adds a Bike column going forward.

The **Workflow expectations** section is updated to thread `--bike <slug>` into the FIT and GPX flow steps, and adds a surface-selection step for the GPX flow when the bike supports multiple surfaces.

### Physics model (`physics_model.py`) changes

Three changes inside the module:

**Per-bike configuration**: replace hardcoded constants with a `BikeConfig` dataclass passed into `solve_speed`:

```python
@dataclass
class BikeConfig:
    bike_weight_kg: float
    cda: float
    drivetrain_efficiency: float
    wheel_circ_m: float
    crr_by_surface: dict[str, float]
    fr_split: tuple[int, int]
    assist: AssistConfig | None

def solve_speed(rider_power_w, grade, bike: BikeConfig, surface: str,
                system_weight_kg, air_density=1.225) -> SpeedResult:
    crr = bike.crr_by_surface[surface]
    # remainder unchanged — uses bike.* instead of module-level constants
```

**Assist augmentation** (new function for motorised bikes):

```python
def solve_speed_with_assist(rider_power_w, grade, bike, surface,
                            system_weight_kg, assist_level="L1") -> AssistedSpeedResult:
    cutoff_mps = bike.assist.cutoff_kph / 3.6
    motor_max_w = bike.assist.rated_w
    level_share = bike.assist.level_share[assist_level]
    # iterative solve: motor contributes min(motor_max_w, rider_power_w * level_share)
    # at speeds below cutoff_mps; above cutoff, motor_w = 0.
    # Output: rider_w, motor_w, total_wheel_w, speed_kph, wh_consumed_per_hour
```

**Brompton output shape** (per-bike output template):

| Tripster column | Brompton equivalent |
|---|---|
| Power @ FTP (W) | HR-zone target (Z2 / Z3) |
| Power @ MAP (W) | HR-zone target (Z4 max) |
| Speed @ FTP (km/h) | Speed @ L1 holding Z2 (km/h) |
| Speed @ MAP (km/h) | Speed @ L2 holding Z3 (km/h) |
| Climb time @ FTP (min) | Climb time @ L2 (min) |
| Climb power req (W) | Recommended assist level + HR zone |
| — | Battery drain estimate (Wh used vs 345 Wh) |

No wattage is quoted to the rider for Brompton rides. All prescriptions are HR-zone + assist-level instructions. Wattage stays internal for speed and battery math.

### Tyre pressure (`tyre_pressure.py`) changes

The script becomes bike-aware:

```bash
python scripts/tyre_pressure.py --bike tripster --surface tarmac --system-weight 94
python scripts/tyre_pressure.py --bike brompton-g --surface gravel --system-weight 100
```

Behind the flag it reads `bikes[slug].tyres` (size + measured width or ETRTO) and `bikes[slug].fr_split` to drive the Silca model. The `--surface` flag is required for bikes with more than one supported surface; ignored otherwise.

A new pressure block in `USER_PROFILE.md` for the Brompton holds indicative starting points (28–35 psi front depending on surface) clearly flagged as **not yet validated**. The script's output for the Brompton prints a "pressures indicative, not validated for this bike" notice until the F/R split is measured.

Silca's empirical validation envelope is ~40 mm; the 54-406 G-One sits outside it. The script flags this in its output rather than implying precision we don't have.

### New-bike onboarding checklist

When adding a bike to the `bikes:` dict, the indicative pressure block in `USER_PROFILE.md` must be backed by a fresh Silca lookup against the bike's actual tyre / weight / surface combination — not extrapolated from another bike's table.

Canonical lookup is **agent-driven via a Chrome browser** at `https://silca.cc/pages/sppc-form`. A research agent prompt template lives at `docs/prompts/silca-pressure-lookup.md` and drives the form, reads the recommended front + rear psi back, and returns a structured result the script can consume directly. Required inputs (collected once per bike, once per surface):

- **Tyre size** (ETRTO width × diameter, e.g. 54-406 for Brompton)
- **Measured tyre width** if it differs from the labelled size
- **System weight** (rider + bike + kit + battery if applicable; per-bike `system_weight_kg_default`)
- **F/R weight distribution** (must be measured first; placeholder Silca default if unmeasured, with a "pressures not validated" flag in output)
- **Tube type** (TPU / latex / butyl / tubeless) — affects break-point above which CRR climbs
- **Surface** — run the lookup once per surface in `surfaces_supported` (Brompton: once for tarmac, once for gravel)

The agent reports each run as a structured block (inputs echoed + outputs front_psi/rear_psi + a screenshot URL of the calculator result for the audit trail). Values populate the bike's pressure block in `USER_PROFILE.md`; the script reads them per `--surface`. The lookup is repeated whenever any input changes materially (new tyres, sustained ±2 kg weight shift, new measured F/R split).

**Manual fallback** at `https://silca.cc/pages/sppc-form` is the alternative when Chrome tooling isn't available to the agent (e.g. on a headless CI environment without Playwright). The required inputs and outputs are the same.

For the Brompton G Line, the first lookup is **blocked on the pending F/R split measurement**. Until that lands, the pressure block holds indicative ranges flagged "not yet Silca-validated for this bike".

### Battery calibration loop

When ingesting a Brompton FIT, the agent prompts for **battery percentage at start and finish** and the **rider's assist-level pattern for the ride** (e.g. "L1 default, L2 on the three flagged climbs"). These get recorded in the ride analysis markdown header alongside the bike slug, and a one-line summary lands in the Ride log entry. After ~5–10 rides covering all levels, the empirical Wh-per-km at each level replaces the placeholder `level_share` multipliers via a simple least-squares fit. The first calibration data point is logged from tomorrow's gravel route.

## Implementation rollout

Schema-first, scripts incrementally, to avoid breaking existing Tripster analyses:

1. **Phase 1 — schema**: add `bikes:` to `USER_PROFILE.md` with `tripster:` populated from current `physics:`. Keep `physics:` as a temporary alias for `bikes[default_bike]`. Add Brompton block with research findings + placeholders.
2. **Phase 2 — loader**: build `scripts/profile_loader.py` with `load_bike()` and the shared CLI helper for the `--bike` flag (hard-fail + stderr warning).
3. **Phase 3 — migrate scripts** in dependency order:
   - `physics_model.py`
   - `analyse_gpx.py` (unblocks the gravel route)
   - `analyse_fit.py` + `analyse_climbs.py`
   - `tyre_pressure.py`
   - `training_load.py`
4. **Phase 4 — remove alias**: drop the temporary `physics:` block in `USER_PROFILE.md` once all scripts read from `bikes:`.
5. **Phase 5 — acceptance test**: run the Long Gravel route through `analyse_gpx.py --bike brompton-g --surface gravel`. First real validation of the Brompton physics + assist model. Log battery start/finish for the first calibration data point.

While the migration is in flight (Phases 1–4), **don't run any analysis on real data**. The acceptance test is the first run after Phase 4 finishes.

## Ride log changes

Add a **Bike** column between Date and Type in the Ride log table of `USER_PROFILE.md`. Backfill: every existing row except the May 12 Cély rides is Tripster; those two are Brompton. New rows record the bike slug.

## Pending experiments (added to `USER_PROFILE.md`)

```markdown
## Brompton G Line calibration (added 2026-05-12)
- **F/R weight split** — measure on bathroom scales at typical commute kit weight
- **Tyre pressure validation** — 3–5 rides comparing comfort/rolling on 54-406 G-One
  at tarmac vs gravel pressures
- **Assist level multipliers** — refine L1/L2/L3 placeholders (0.5/1.0/1.5) by
  comparing HR-effort and Wh-consumption across logged rides at each level
- **Battery drain calibration** — log start/finish battery % for each Brompton ride
  to fit Wh-per-km at each assist level (vs 30–60 km / 345 Wh manufacturer claim)
- **CdA estimate** — placeholder 0.42 from review-position notes; refine if a
  flat-windless calibration ride becomes feasible on the G Line
```

## Things that explicitly DON'T change

- TSS / CTL / ATL formulas — only the *source* of TSS changes (Brompton = hrTSS only)
- DEM verification, OSRM map-matching, hi-fi chart generation — terrain is bike-agnostic
- Power zones, HR zones, fuelling protocols — rider-keyed, not bike-keyed
- Training load source hierarchy (TP > scripts) — unchanged
- Existing Tripster analyses — not retrofitted

## Research references (informing this design)

Brompton G Line Electric technical reference compiled 2026-05-12:

- Brompton — Electric G Line product page: https://www.brompton.com/electric-g-line
- Brompton — 4-speed product page with full spec list: https://www.brompton.com/p/1346/electric-g-line-with-roller-frame-4-speed
- Brompton — e-Motiq engineering story: https://www.brompton.com/stories/design-and-engineering/brompton-e-motiq-development
- PopSci review: https://www.popsci.com/gear/brompton-electric-g-line-folding-gravel-ebike-review/
- CyclingNews review: https://www.cyclingnews.com/reviews/brompton-g-line-review/
- Cycling Weekly review: https://www.cyclingweekly.com/reviews/e-bikes/bromptons-electric-g-line-a-fun-to-ride-commuter-friendly-bike-that-can-go-everywhere-and-anywhere-with-you

Power-meter feasibility research compiled 2026-05-12 — Favero Assioma PRO MX-2 viable at ~£650 but conflicts with QR fold; Stages and 4iiii crank-arm power meters do not fit the Brompton spider crankset. Conclusion: skip the power meter.

Per-level assist magnitudes (L1/L2/L3 multipliers) are not published by Brompton and not measured in any reviewed source. The `level_share` placeholders in the schema are explicitly flagged for calibration over the first 5–10 logged rides.
