# Climb Verifier — Design Spec

**Date:** 2026-05-10
**Status:** Draft for review

## Problem

Routing-engine GPX files (e.g. RideWithGPS, Komoot) systematically under-report peak gradients. Sample measurements (from comparing routing-engine GPX against the actual FIT recorded on a representative ride with several Cat-3-equivalent climbs):

| Climb | km | FIT peak | GPX v2 peak | Δ | Steep section length |
|---|---|---|---|---|---|
| C2 | 6.75 | 14.3% | 9.3% | −5.0pp | 116m of >12%, 192m of >10% |
| C3 | 12.85 | 12.7% | 6.9% | −5.8pp | 27m of >12%, 114m of >10% |
| C5 | 24.65 | 9.3% | 8.2% | −1.1pp | 79m of >8% |
| C6 | 37.50 | 7.3% | 6.0% | −1.3pp | — |
| C7 | 46.55 | 10.6% | not detected | — | 30m of >10%, 82m of >8% |
| C8 | 47.95 | 13.6% | 7.6% | −6.0pp | 85m of >12%, 225m of >10% |

Two failure modes:
- **Gradient smoothing**: routing engine resamples at ~46m and waypoint-interpolates, smearing peaks into averages. Consistent ~5–6pp under-report on Cat 3 climbs.
- **Missed climbs**: short steep climbs (e.g. Climb 7 at km 46.55) drop out of the routing GPX entirely.

This causes pacing errors: planning a Cat 3 at "9% peak" on a climb that actually peaks at 14% leads to gear and power miscalculation.

## Goal

Detect both failure modes pre-ride by re-sampling the route against a higher-fidelity elevation source and producing a **Fidelity Report** that flags climbs where the GPX understates peak gradient or misses climbs entirely.

## Non-goals

- Fixing the routing engine's GPX output. We verify and report; we do not re-route.
- Per-second power/pacing recommendations. The existing `analyse_gpx.py` handles that — the verifier feeds it corrected gradients.
- Real-time on-bike use. This is a pre-ride planning tool.
- General-purpose elevation API client. Scope is climb verification only.

## Architecture

### Two-tier verification (simplified from original 3-tier)

The original spec proposed Tier 1 (zero-cost) → Tier 2 (cheap mid-res) → Tier 3 (expensive high-res) to ration paid API calls. Investigation showed:
- 30m DEMs (Open-Elevation, Stadia) cannot resolve the spikes any better than the GPX itself for short walls.
- Free 1m lidar is available for both primary regions (UK + France) via on-device DEM tiles.

So the cost-rationing logic is unnecessary. Local lidar queries are free and fast; we run them on every candidate climb.

```
Tier 1 — Baseline (existing analyse_gpx.py)
  └─ identify candidate climbs from GPX trackpoints
       │
       ▼
Tier 2 — High-fidelity verification (NEW)
  ├─ for each climb: resample the climb's coords against local 1m DEM
  │      at ≤5m spacing
  ├─ compute true peak gradient, length-of-section above 8/10/12/14%
  └─ if coords outside loaded tiles → fallback to GPXZ free API
       │
       ▼
Missed-climb sweep (NEW)
  └─ walk the entire route at 25m stride against the local DEM
       and flag any segment matching the climb-detection thresholds
       that the Tier 1 detector missed
       │
       ▼
Fidelity Report
```

### Components

**`scripts/local_dem.py`** — DEM tile loader and point-sampler.
- Wraps `rasterio` to query elevation at lat/lon.
- Supports a directory of GeoTIFF tiles indexed by bounding box.
- Bilinear interpolation for sub-cell sampling.
- Returns `None` (not an error) if a coord falls outside loaded tiles, so the caller can fallback.
- Public API: `sample(lat, lon) -> Optional[float]`, `sample_polyline(coords, stride_m) -> List[Optional[float]]`.

**`scripts/elevation_fallback.py`** — GPXZ free-tier client.
- Used only when local DEM returns `None` for any point on a climb.
- Batches up to 512 points per call (free-tier limit: 100 req/day, 1 rps — plenty).
- Reads API key from `~/.config/cycling-coach/gpxz.key` (gitignored path).
- If no key configured and local DEM misses, the verifier reports "outside coverage" rather than failing.

**`scripts/verify_climbs.py`** — orchestrator.
- Inputs: GPX path, optional `--out` for report path.
- Calls `analyse_gpx` to get baseline climbs.
- For each climb, resamples via `local_dem` (with `elevation_fallback` if needed) and recomputes peak gradient and steep-section lengths.
- Runs missed-climb sweep over the full route.
- Emits the Fidelity Report.

**`scripts/fetch_dem_tiles.py`** — bulk tile downloader.
- Inputs: `--bbox <minlon,minlat,maxlon,maxlat>` or `--gpx <path>` (derives bbox from route) or `--region <name>` (preset regions: `surrey-kent`, `greater-london`, `ile-de-france`, etc.).
- Resolves the bbox to OS-grid (UK) or IGN-grid (FR) tiles.
- Pulls from DEFRA / IGN public endpoints.
- Writes to `~/cycling-coach-dem/{uk-1m,fr-1m}/<grid>/<tile>.tif` and updates `coverage.json`.
- Idempotent: skips tiles already present and intact (size + hash check).
- Resume-safe: partial downloads are deleted on failure.

**`scripts/analyse_gpx.py`** — integration.
- `verify_climbs` runs **by default** after the existing pipeline. Output: Fidelity Report inlined into the prediction MD between `<!-- BEGIN FIDELITY -->` / `<!-- END FIDELITY -->` markers (idempotent — re-runs replace the block).
- `--no-verify` flag opts out (e.g. for offline use without DEM tiles).
- If verification fails entirely (no tiles, no API), the prediction MD is still written with a "Verification skipped: <reason>" note in place of the report.

### Data layout

```
~/cycling-coach-dem/         # outside repo, gitignored if symlinked in
  uk-1m/
    SU/  SU_*.tif            # OS grid tiles (Surrey, Kent, Sussex, Greater London)
    TQ/  TQ_*.tif
  fr-1m/
    ile-de-france/           # IGN RGE ALTI, Fontainebleau region
```

Tile-coverage manifest (`~/cycling-coach-dem/coverage.json`) records what's loaded. `local_dem.py` reads this on init.

### Fidelity Report format

Markdown, written to `routes/<name>-fidelity.md` (parallel to existing `-prediction.md`):

```markdown
# Fidelity Report — <route name>

**Verified against:** UK 1m LIDAR Composite (DEFRA, OGL v3) — 2022
**Verified on:** 2026-05-10
**Coverage:** 100% (0 fallback calls)

## Verdict: HIGH RISK — peak gradients underestimated on 3 climbs

## Per-climb comparison

| # | Where | GPX peak | Verified peak | Δ | >12% length | >10% length |
|---|---|---|---|---|---|---|
| 1 | km 6.75 | 9.3% | **14.3%** | +5.0pp | 116m | 192m |
| ... |

## Missed climbs (in DEM, not in GPX)

| Where | Length | Gain | Avg % | Peak % |
|---|---|---|---|---|
| km 46.55 | 650m | 32m | 5.0% | 10.9% |

## Coverage notes
(any fallback calls or out-of-coverage flags)
```

A "Verdict" line summarises:
- **Safe to plan** — all Δ within ±1pp, no missed climbs.
- **Minor risk** — Δ up to 2pp, no missed climbs.
- **High risk** — any Δ >2pp or any missed climb.

### Coverage-gap UX (interactive prompt)

When the route falls partly or fully outside loaded local DEM tiles, the verifier identifies the missing tiles up-front (before any sampling) and prompts the user:

```
Route extends outside loaded DEM tiles.
Missing tiles: TQ45, TQ55  (~120 MB total, IGN/DEFRA, free)

Options:
  [d] Download missing tiles now and verify locally   (recommended)
  [a] Use GPXZ API for the uncovered segments only
  [s] Skip verification on uncovered segments and proceed
  [q] Quit

Your choice [d]:
```

The prompt is shown only when stdin is a TTY. For non-interactive use (CI, batch scripts), the policy is set by a flag:

- `--coverage-gap=download` (default for interactive)
- `--coverage-gap=api` (default for non-interactive if GPXZ key configured)
- `--coverage-gap=skip` (default for non-interactive if no GPXZ key)
- `--coverage-gap=fail` (treat missing tiles as a hard error)

The chosen policy is recorded in the Fidelity Report's "Coverage notes" section.

### Failure-mode handling

| Condition | Behaviour |
|---|---|
| Local DEM tile missing for a coord | Apply coverage-gap policy (prompt or flag, see above) |
| User chooses download but fetch fails | Fall through to API; if no API, fall through to skip; report each step taken |
| GPXZ rate-limited or down | Flag affected climb as "unverified", continue rest of report |
| GPX has no climbs | Skip Tier 2, run missed-climb sweep, report "no climbs detected" |
| Local DEM returns NaN at a point (cell holes) | Bilinearly interpolate from neighbours; if ≥3 contiguous holes, flag |
| Out-of-coverage region (e.g. Wales mountains, Alps) | Coverage-gap UX kicks in; if user picks "api" GPXZ verifies the whole route, report `Verified against: GPXZ 1m composite` |

## Operational constraints

- **Conda env portability**: any new dependency goes into `environment.yml` with top-level loose pins (e.g. `rasterio>=1.3`), no build hashes, no platform channels. Must resolve cleanly on osx-arm64 and linux-64. New deps expected: `rasterio`, possibly `pyproj`. The conda-env-portability rule is also added to `CLAUDE.md` as a persistent project rule.
- **Personal data**: DEM tiles, API keys, and the coverage manifest live outside the repo (`~/cycling-coach-dem/`, `~/.config/cycling-coach/`). Nothing personal is committed.
- **Offline-first**: the primary path (local DEM) does not require network. Fallback is the only network dependency.
- **Cost ceiling**: zero. GPXZ free tier (100 req/day) is the only paid-vendor touchpoint and we stay strictly within it.
- **Licensing**: UK LIDAR Composite (OGL v3) and IGN RGE ALTI (Licence Ouverte 2.0) both permit personal use and redistribution. We document tile provenance in `coverage.json`.

## Test plan

- **Regression on sample-route**: `verify_climbs routes/2026-05-09-sample-route.gpx` must report C2/C3/C8 as "underestimated >5pp" and detect the missed Climb 7 at km 46.55.
- **Coverage gap**: synthetic GPX with a coord in Iceland → verifier falls back to GPXZ (or flags out-of-coverage if no key).
- **No-climb route**: a flat city park loop GPX → reports "no climbs detected", no errors.
- **Cross-platform conda solve**: `conda env create -f environment.yml` succeeds on osx-arm64 and linux-64 (CI optional, manual ok).

## Decisions log

- **Fidelity Report placement**: inlined into `-prediction.md` between sentinel markers (single source of truth per route, easier to read alongside pacing narrative).
- **Tile download**: scripted via `scripts/fetch_dem_tiles.py` with bbox / GPX / region inputs, idempotent and resume-safe.
- **`analyse_gpx.py` verification**: default-on; `--no-verify` opts out.
