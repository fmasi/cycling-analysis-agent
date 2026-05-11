# Cycling Analysis Agent

A Python framework for cycling coaches and self-coached riders. It reads FIT
files, analyses rides, predicts performance from GPX routes, tracks training
load over time, and maintains a structured rider profile that any Claude
Code-compatible assistant can use as long-term memory.

The framework is designed to be **rider-agnostic**: all personal data lives
in a single gitignored `USER_PROFILE.md` file, leaving the repo safe to
publish, fork, or share. Scripts read the profile at import time and fall
back to neutral defaults so a fresh clone runs out-of-the-box.

## Features

- **FIT analysis** — parses TrainingPeaks-stored TSS/NP/IF, builds a power
  curve, identifies climbs, and writes a per-ride Markdown analysis.
- **GPX route prediction** — identifies climbs, predicts speed/duration at
  FTP/MAP/Z2/Z3 with explicit uncertainty ranges, generates an overview
  chart, and writes a pacing narrative.
- **Hi-fi DEM climb verifier** — re-samples each GPX climb against 1 m lidar
  DEM tiles (UK DEFRA / FR IGN) to catch the routing-engine peak under-
  reports that ruin pacing on Cat-3+ climbs. Falls back to the GPXZ free
  API for coverage outside locally-cached tiles.
- **Training load projector** — CTL / ATL / TSB forecasting using
  TrainingPeaks' lag-1 convention.
- **Climb categorisation** — UCI-style Cat 4 / Cat 3 / Cat 2 / Cat 1 / HC
  bucketing with a Tour-de-France-scale KOM-points total per ride.
- **Tyre pressure calculator** — Silca-extrapolated for any measured F/R
  weight split, not just the 50/50 to 46.5/53.5 preset range.
- **Physics model** — a single `predict_speed` / `predict_power` pair built
  on the standard cycling power equation, parameterised by the rider's
  CdA, CRR, drivetrain efficiency, and system weight.

## Quick start

```bash
git clone <this repo>
cd cycling-analysis-agent

# Create the conda env (osx-arm64 / linux-64; loose pins, cross-platform)
conda env create -f environment.yml
conda activate cycling

# Copy the profile template and fill in your numbers
cp USER_PROFILE.example.md USER_PROFILE.md
# edit USER_PROFILE.md — at minimum the YAML frontmatter

# Confirm everything loads
python scripts/physics_model.py
python scripts/profile.py
bash scripts/smoke_test.sh

# Drop a FIT file in rides/ and analyse it
python scripts/analyse_fit.py rides/<your-file>.fit --save

# Drop a GPX file in routes/ and predict it
python scripts/analyse_gpx.py routes/<your-route>.gpx --save
```

## Using with Claude Code

The framework includes a `CLAUDE.md` instruction file that any
Claude Code-compatible assistant (Claude Code, OpenClaude, etc.) will
auto-load when started in the repo root. It contains the coaching workflows,
physics formulas, training-load definitions, and the conventions every
script follows. The assistant reads `USER_PROFILE.md` to learn who you are,
then uses the scripts to produce analyses and updates the profile in place
as a side effect of every workflow.

You don't need Claude Code to use the scripts — they all run standalone.
But the assistant is what turns the framework into an everyday coach.

## Folder structure

```
cycling-analysis-agent/
├── CLAUDE.md                   The brain. Auto-loaded by Claude Code.
├── README.md                   This file.
├── LICENSE                     MIT.
├── environment.yml             Conda env (cross-platform, loose pins).
├── USER_PROFILE.example.md     Profile template — copy to USER_PROFILE.md.
├── scripts/                    All analysis tools.
│   ├── profile.py              Loads USER_PROFILE.md (with defaults).
│   ├── physics_model.py        Speed/power/grade equations.
│   ├── analyse_fit.py          FIT parser (post-ride).
│   ├── analyse_gpx.py          GPX parser (pre-ride).
│   ├── analyse_climbs.py       UCI-style climb categorisation + charts.
│   ├── verify_climbs.py        Hi-fi DEM verifier (the climb verifier).
│   ├── local_dem.py            Rasterio-backed local DEM tile sampler.
│   ├── elevation_fallback.py   GPXZ API client.
│   ├── fetch_dem_tiles.py      Bulk DEM tile fetcher (UK DEFRA + FR IGN).
│   ├── make_dem_shapefile.py   OSGB shapefile builder for UK DEM portal.
│   ├── map_match.py            OSRM map-matching for GPX climb coords.
│   ├── chart_overview.py       Route overview chart (3-row layout).
│   ├── chart_overview_verified.py  Stitched hi-fi overview chart.
│   ├── chart_verify_compare.py Hi-fi vs GPX comparison chart.
│   ├── cross_validate.py       Verifier-vs-FIT regression harness.
│   ├── fit_to_gpx.py           Emit a FIT's actual road geometry as GPX.
│   ├── training_load.py        CTL/ATL/TSB projector.
│   ├── tyre_pressure.py        Silca-extrapolated tyre pressures.
│   ├── smoke_test.sh           Quick sanity check.
│   └── _tests/                 pytest suite for the framework code.
├── docs/superpowers/           Design specs + implementation plans.
├── examples/                   Sample outputs (fictional rider).
├── notes/                      Generic research summaries (e.g. strength).
├── rides/                      Your FIT files + per-ride analyses (gitignored).
├── routes/                     Your GPX files + predictions (gitignored).
├── tests/                      Your test records (4DP, Half Monty) (gitignored).
├── plans/                      Your training plans (gitignored).
└── body-comp/                  Your weight/photo/tape logs (gitignored).
```

## Personal-data protection

Six directories are reserved for your data and are gitignored by design:
`rides/`, `routes/`, `tests/`, `notes/` (except the published research
summary), `plans/`, and `body-comp/`. Each has its own internal `.gitignore`
as a second line of defence.

The root `.gitignore` also guards by file type: `*.fit`, `*.gpx`, `*.tcx`,
`*.key`, `.env`, and `*.pem` are never trackable anywhere in the tree.

The single file `USER_PROFILE.md` at the root holds your identity, fitness,
equipment, and history. It is gitignored. Copy from `USER_PROFILE.example.md`
on first setup.

If a script needs a field you haven't filled in, it falls back to a generic
adult-cyclist default defined in `scripts/profile.py`. So a stranger
cloning the repo runs everything immediately — they just get neutral
numbers until they fill in their own.

## Environment

`environment.yml` pins to top-level packages with loose version ranges only,
so it resolves cleanly on both osx-arm64 (Mac) and linux-64. Do not commit
`conda env export` output — it pins architecture-specific build hashes that
break cross-platform reproducibility. Diagnostic exports (e.g.
`environment_export.yml`) are gitignored.

DEM tiles live outside the repo under `~/cycling-coach-dem/{uk-1m,fr-1m}/`.
The GPXZ API key lives at `~/.config/cycling-coach/gpxz.key`. Neither is
required for the core FIT/GPX/training-load workflows — only for hi-fi
climb verification.

## License

MIT. See `LICENSE`.
