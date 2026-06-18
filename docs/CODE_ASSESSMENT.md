# Code Assessment — cycling-analysis-agent

_Whole-codebase audit, 2026-06-19. Five parallel subsystem reviews synthesized._
_~7,600 lines across 23 scripts. Tests cover the verifier/DEM subsystem well; the math core is largely untested._

## Branch decision (resolved)

`fix/frontmatter-block-scalars` is **not stale** — it's an unfinished, more-advanced multi-bike refactor (13 new modules incl. typed `bike_config.py`, 32 commits) implementing the exact `bikes:`/`default_bike:` schema the live `USER_PROFILE.md` already uses. Decision: **salvage** its `bike_config.py` architecture + tests onto current `main` (which has newer uncommitted peer/verifier work the branch lacks), rather than full-merge or drop. Today's inline `profile.py` fix is a band-aid to be replaced by the ported `bike_config.py`.

## Cross-cutting themes (priority order)

1. **Uncommitted work with no safety net.** `compare_riders.py` (874 LoC), `run_peer_compare.sh`, `build_ride_brief_pdf.py` are untracked; `verify_climbs.py` + `CLAUDE.md` modified. Highest risk-of-loss. Commit the framework-generic ones.
2. **Import-time side effects in `profile.py`** (`profile.py:385` `_p = load_profile()` runs at import, reading the real gitignored profile). Makes every `from profile import …` consumer fragile and hard to test, and a single bad profile field crashes all scripts. Keystone fix — unblocks everything else.
3. **Hand-rolled YAML parser bugs (HIGH correctness).** `_parse_simple_yaml` block-scalar (`key: |`) bodies leak their indented lines as sibling keys — can silently corrupt real `fitness:`/`physics:` fields. Quoted-value-with-comment and list parsing also broken. The salvaged typed config layer is the proper fix.
4. **Math core has zero tests** — `physics_model`, `training_load`, climb categorisation, TSS, tyre-pressure F/R. These are the numbers quoted to the rider, and two have regressed before (tyre F/R 48/52 bug, TSB lag).
5. **Duplication.** `find_climbs`/`compute_max_grade`/`median_filter_1d` triplicated (analyse_fit vs analyse_gpx, with a subtle divergence); power metrics (NP/VI/zones/peak-curve) reimplemented in `compare_riders`; `bbox_from_gpx` duplicated.

## Confirmed bugs by severity

### HIGH
- **`profile.py:136-167`** — block-scalar leak corrupts sibling keys (a `note: |` body with `ftp_w: 999` would inject it).
- **`local_dem.py:45-47`** — half-pixel offset in bilinear DEM sampling (treats affine pixel-corner coords as centres). Biases the peak-25m gradient — the headline metric the verifier exists to produce. Invisible to the current constant-slope test.
- **`analyse_gpx.py:228-237`** — `estimate_tss` `flat_km = distance_km − climb_km` can go negative on climb-dense routes (climb lengths off the 50 m grid aren't clamped) → understated time/TSS, no guard.
- **`run_peer_compare.sh:38-44,59-72`** — unquoted heredoc interpolates raw FIT path into Python source: breakage / injection on any path with `"`,`$`,backtick,newline.
- **`build_ride_brief_pdf.py:7`** — hardcoded container root `/home/node/...`; not portable, no existence checks. One-off, not a reusable tool.

### MED
- **`analyse_gpx.py:190-211,336`** — `predict_climb` freezes profile FTP/MAP into dict keys + prose (`'speed_kmh_FTP_171'`); a future FTP re-test desyncs the numbers and `KeyError`s `format_markdown`.
- **`physics_model.py:122-129`** — `zone_for_power` first-match over overlapping zones makes **Z4 Sweet Spot unreachable** (0.88·FTP → "Z3").
- **`physics_model.py:59-63`** — no descent/terminal cap; `predict_speed(0,−10)` → ~80 km/h reported as a coaching number.
- **`verify_climbs.py:318,344`** — a fully DEM-uncovered climb reports as a benign `0.0%` peak (large negative Δ), never flagged "unverified"; the `isnan` render branch is dead.
- **`compare_riders.py:209-211,264`** — bracket access `arr["altitude_m"]` `KeyError`s on power-only/no-baro FITs; `ftp=0` fallback makes `detect_flat_attacks` count every sample as a surge; EF delta `ef2/ef1` unguarded divide-by-zero.
- **`profile.py:295-297`** — `float(bike[src])` at import time crashes all scripts if a bike field is a non-numeric string.
- **`elevation_fallback.py:59`** — no `len(results)==len(chunk)` check; API reorder/drop misassigns elevations.
- **Uncommitted `verify_climbs.py` wall-density/TSS-rewrite (~250 LoC regex markdown surgery + 1-point calibration) ships with zero tests.**

### LOW
- `analyse_climbs.py:100` NP is 4th-power mean without 30 s rolling-average (mislabelled). `analyse_gpx.py:60` missing `<ele>` zero-fills instead of interpolating (FIT path already interpolates). `analyse_gpx.py:277` loop detection hardcoded 0.001° (latitude-dependent). `make_dem_shapefile.py:44` stale CCW docstring (code is correctly CW). Various perf warts (Python loops over 1 Hz data: `analyse_fit.py:421`, `compare_riders.py:216`).

## Refactor targets
- Extract **`climb_detect.py`** (`find_climbs`, `compute_max_grade`, `median_filter_1d`) — kills the triplication + divergence.
- Extract **`power_metrics.py`** (NP, VI, time-in-zones, peak-power curve) — shared by analyse_fit + compare_riders.
- Extract **`geo_util.py`** (`bbox_from_gpx`, haversine, semicircle conversion).
- Split **`verify_climbs.py`** (1253 LoC) into `grade_analysis` / `dem_sampling` / `profile_stitch` / `report_embed`.
- Separate **computation from markdown templating** in all three `analyse_*` entry points (the reason they're untestable).
- Standardize matplotlib on explicit `fig.savefig`/`plt.close(fig)` (chart_overview.py is the odd one out).

## Test + infra plan
- **`pyproject.toml`** `[tool.pytest.ini_options]`: `testpaths=["scripts/_tests"]`, `pythonpath=["scripts"]` (removes per-file `sys.path.insert`), `addopts="-q"`.
- **Make `profile.py` import side-effect-free** (lazy `lru_cache` load + `__getattr__` constants) → unblocks testing all consumers.
- **`conftest.py`**: add `synthetic_profile` fixture (generalize the SAMPLE block already in `test_profile_bike.py`); move `sys.path` bootstrap here.
- **`test_smoke_imports.py`**: import every `scripts/` module — highest-leverage single test given the import-time risk.
- **Pure-math suites (P1):** `physics_model` (power↔speed round-trip, Z4, descent), `training_load` (EMA α, lag-1 TSB, boundary table), `analyse_climbs` (category boundary table), `tyre_pressure` (F/R regression guard).
- **Loader suite (P2):** `load_profile`/`power_zone_bounds`/`load_peer` via synthetic-profile fixture.
- **FIT/GPX (P3):** extract NP/TSS/peak-power as pure functions, test on synthetic series.
- **CI:** minimal GitHub Actions — build env from `environment.yml`, run `pytest scripts/_tests`. Suite is fully hermetic (no network/real-DEM/real-profile once P2 lands).

## Execution phases
- **P0 — safety + infra:** commit untracked framework scripts; add pyproject pytest config + smoke test.
- **P1 — salvage foundation:** lazy `profile.py`; port `bike_config.py`; replace band-aid; fix block-scalar parser; synthetic-profile fixture + loader/bike tests.
- **P2 — correctness fixes w/ tests:** local_dem half-pixel, estimate_tss clamp+range, predict_climb de-hardcode, zone_for_power/descent, compare_riders hardening, shell-injection, DEM-miss flagging.
- **P3 — dedup/refactor:** climb_detect, power_metrics, geo_util; split verify_climbs; computation/markdown separation.
- **P4 — CI + coverage expansion.**
