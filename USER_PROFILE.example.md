<!--
  USER_PROFILE.example.md — TEMPLATE
  Copy this file to USER_PROFILE.md and fill in your data.
  USER_PROFILE.md is gitignored — your data stays local.
  YAML frontmatter is required. Markdown sections are filled in as you accumulate data.
-->

---
# The YAML frontmatter is read by scripts/profile.py at import time.
# Any field you omit falls back to a generic adult-cyclist default
# (see DEFAULTS in scripts/profile.py).
identity:
  name: Generic rider
  dob: YYYY-MM-DD
  location: <City / area>
  height_m: 1.78

body:
  weight_kg: 75.0                 # Used for W/kg calculations
  weight_source: <e.g. "Withings AM fasted">
  weight_updated: YYYY-MM-DD

fitness:
  ftp_w: 200                      # Functional Threshold Power
  ftp_updated: YYYY-MM-DD
  map_w_working: 250              # Maximal Aerobic Power — what you train against
  map_w_test: 250                 # Raw test value
  map_note: <any caveats about MAP value>
  ac_w: 350                       # Anaerobic Capacity (1-min power)
  ac_note: <caveats>
  nm_w: 600                       # Neuromuscular peak (5–15s)
  lthr_bpm: 165                   # Lactate Threshold HR (cycling)
  max_hr_bpm: 190
  rest_hr_bpm: 55                 # Used by Karvonen HR zones
  max_hr_uncertainty: <e.g. "±5">

# Per-bike physics live under a `bikes:` registry; `default_bike` names the
# active one. scripts/profile.py folds the active bike's values into the
# physics constants (SYSTEM_WEIGHT_KG, FR_SPLIT_FRONT_PCT, CdA, …); the full
# typed API (gearing, surfaces, e-assist) is scripts/bike_config.py:load_bike().
# Override the bike per-run with `--bike <slug>` where supported.
default_bike: roadbike

bikes:
  roadbike:
    name: <Make / model>
    bike_weight_kg: 9.0
    system_weight_kg_default: 87.0   # body + bike + kit, used for speed/power
    fr_split: "48/52"                # Front/rear weight split (Silca default 48/52)
    cda: 0.30                        # Coefficient of drag × frontal area
    cda_range: "0.28-0.32"
    drivetrain_efficiency: 0.97      # 0.97 for 2x/3x; 0.98 for direct-drive
    wheel_circ_m: 2.155              # 700c × 32mm — adjust per tyre
    has_power_meter: true
    tyres:
      model: <tyre model>
      size_mm: 32
    crr_by_surface:                  # rolling resistance per surface
      tarmac: 0.0055                 # intermediate pressure; 0.0050 latex/TPU optimal
    surfaces_supported: [tarmac]
    # Optional: gearing for min-gear/cadence pacing
    # gearing:
    #   chainrings_t: [34, 50]
    #   cassette_t: [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]
    # Optional: e-assist block for motorised bikes — see bike_config.AssistConfig

# Environment constants (not bike-specific). Defaults applied if omitted:
# air_density_kg_m3: 1.225  (sea level, 15°C); gravity_m_s2: 9.81;
# crr: 0.0055 (fallback when a surface isn't in crr_by_surface).
#
# Single-bike setups may instead use a flat top-level `physics:` block
# (bike_weight_kg / system_weight_kg / cda / fr_split_front_pct / …) — the
# loader still supports it for backward compatibility.

training_load:
  ctl: 0                          # Chronic Training Load — 42-day EMA of TSS
  atl: 0                          # Acute Training Load — 7-day EMA of TSS
  tsb: 0                          # Training Stress Balance — yesterday CTL − yesterday ATL
  source: <e.g. "TrainingPeaks AM YYYY-MM-DD">

# Riding-partner (peer) registry — flat `peer_<lowername>:` sections,
# consumed by scripts/profile.py:load_peer() and scripts/run_peer_compare.sh
# (the rider-vs-peer FIT comparison wrapper). Add one block per peer.
# Qualitative notes (style, strengths, planning rules) live in the
# "Riding partners" section in the markdown body below.
# Example block:
#
# peer_<name>:
#   label: <Display name>
#   ftp_w: 200                    # Your best calibrated estimate of their FTP
#   ftp_w_stored: 220             # Optional — what their head unit reports
#   ftp_source: garmin-auto       # test | garmin-auto | self-declared | unknown
#   weight_kg: 75                 # Estimated; used for W/kg comparisons
#   bike_summary: "<one-line bike spec relevant to riding-together comparisons>"
#   last_compared_ride: "<YYYY-MM-DD short route name>"

goals:
  primary_event: <Event name>
  primary_date: YYYY-MM-DD
  ftp_target_wkg: 3.0
  phase: <e.g. "BUILD" | "BASE" | "PEAK" | "RECOVERY">
---

# Rider context

Free-form Markdown. Use sections for narrative, uncertainty, and history that doesn't fit a YAML key.

## Asymmetries / injuries
Anything the coach should know about (leg-length difference, prior injuries, posture issues).

## Training history
How long you've been training, prior FTP/MAP progression, key milestones.

# Equipment

## Primary bike
- Frame, drivetrain, cassette, tyres, tubes, sensors. Be specific — physics calculations depend on this.

## Secondary bikes / commute / indoor

## Sensors
- Power meter (single-sided crank, dual-sided, pedal-based — affects accuracy)
- Head unit
- HR strap

## Measurement accuracy notes
Any known biases (e.g. left-only crank reads ~96% of true if you're 48/52 imbalanced).

# Position & fit

| Measurement | Value | Notes |
|---|---|---|
| Saddle height | mm | BB centre to top |
| Saddle setback | mm | Tip of saddle behind BB |
| Stem length | mm | |
| Stem angle | degrees | |
| Bar width | cm | |
| Position character | upright / neutral / aggressive | |

## Weight distribution
Measured F/R split if you have scales. Defaults to Silca's 48/52 if unknown.

# Tyre pressure preferences

Once calibrated via experimentation, record your preferred surface targets here.

# Power zones

Generated from FTP. Cache here for readability.

# HR zones

Generated from max + rest HR via Karvonen.

# Current fatigue context

Narrative around the YAML training_load block. Recent sessions, why TSB is where it is.

# Training priorities

Personal split: MAP / FTP / endurance / strength weighting.

# Fuelling protocol

Carb-per-hour targets per ride type, real-food vs engineered preference, alarm/stop pattern.

# Body composition tracking

Weight log, tape log, photo log tables.

# Goals (detail)

Primary event narrative — key climbs, gearing checks, predicted speeds.

# Pending experiments

# Open questions

# Data status tracker

| Item | Status | Last updated |
|---|---|---|
| FTP | | |
| MAP | | |
| ... | | |

# Ride log

| Date | Type | Distance/Time | TSS | Analysis file |
|---|---|---|---|---|
