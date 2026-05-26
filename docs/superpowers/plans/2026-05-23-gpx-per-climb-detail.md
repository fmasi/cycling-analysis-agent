# Per-climb detail in GPX route planning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-climb zoom charts + gear/cadence pacing to the GPX route-planning workflow (`analyse_gpx.py`), triggered by a significance gate that catches short steep climbs, reusing the existing `analyse_climbs` renderer.

**Architecture:** Extract the per-climb renderer and categorisation from `analyse_climbs.py` into shared modules (`chart_climb_detail.py`, `climb_categories.py`) imported by both the FIT and GPX paths. Add a pure `gearing.py` helper. Wire selection + rendering + a gear/rpm pacing column into `analyse_gpx.py` additively.

**Tech Stack:** Python 3, numpy, matplotlib, pytest. Scripts live flat in `scripts/`; tests in `scripts/_tests/` insert `parents[1]` onto `sys.path` and import modules by bare name.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `scripts/gearing.py` | Pure cadence & gear maths | Create |
| `scripts/climb_categories.py` | `CATEGORIES`, `categorise`, `is_significant`, `select_climbs_for_detail` | Create (move `categorise`/`CATEGORIES` out of `analyse_climbs.py`) |
| `scripts/chart_climb_detail.py` | `plot_climb_detail` + render helpers (`grade_colour`, `resample_segment`, `climb_stats`) | Create (move out of `analyse_climbs.py`) |
| `scripts/analyse_climbs.py` | FIT analysis (now imports the moved functions) | Modify |
| `scripts/bike_config.py` | `BikeConfig` gains optional `gearing` field | Modify |
| `scripts/analyse_gpx.py` | Wire selection + per-climb charts + gear/rpm column | Modify |
| `USER_PROFILE.md` | Add `gearing:` to `bikes.tripster` (NOT committed — gitignored) | Modify |
| `scripts/_per_climb_detail.py` | Throwaway one-off | Delete |
| `scripts/_tests/test_gearing.py` | Unit tests for gearing | Create |
| `scripts/_tests/test_climb_categories.py` | Unit tests for gate/selection/categorise | Create |
| `scripts/_tests/test_chart_climb_detail.py` | Render smoke test | Create |
| `scripts/_tests/test_analyse_gpx_climb_detail.py` | Lo-fi end-to-end integration | Create |

**Note on `USER_PROFILE.md`:** it is gitignored personal data — never `git add` it. The `BikeConfig.gearing` field and its tests use a synthetic profile dict, so tests do not depend on the rider's file. The rider edits their own `USER_PROFILE.md` to add `gearing:` (Task 1, Step 6).

---

## Task 1: Add `gearing` to BikeConfig

**Files:**
- Modify: `scripts/bike_config.py:40-60` (dataclass), `scripts/bike_config.py:112-133` (load_bike)
- Test: `scripts/_tests/test_bike_config_gearing.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_tests/test_bike_config_gearing.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bike_config import load_bike

PROFILE = {
    "default_bike": "tripster",
    "bikes": {
        "tripster": {
            "name": "Test Tripster",
            "bike_weight_kg": 11.6,
            "system_weight_kg_default": 90.1,
            "fr_split": "40/60",
            "cda": 0.28,
            "drivetrain_efficiency": 0.97,
            "wheel_circ_m": 2.155,
            "has_power_meter": True,
            "tyres": {},
            "crr_by_surface": {"tarmac": 0.005},
            "surfaces_supported": ["tarmac"],
            "gearing": {
                "chainrings_t": [30, 39, 50],
                "cassette_t": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32],
            },
        },
        "nogears": {
            "name": "No Gears",
            "bike_weight_kg": 20.0,
            "system_weight_kg_default": 98.0,
            "fr_split": "40/60",
            "cda": 0.42,
            "drivetrain_efficiency": 0.96,
            "wheel_circ_m": 1.59,
            "has_power_meter": False,
            "tyres": {},
            "crr_by_surface": {"tarmac": 0.01},
            "surfaces_supported": ["tarmac"],
        },
    },
}


def test_gearing_parsed_when_present():
    bike = load_bike("tripster", profile=PROFILE)
    assert bike.gearing["chainrings_t"] == [30, 39, 50]
    assert bike.gearing["cassette_t"][0] == 11
    assert bike.gearing["cassette_t"][-1] == 32


def test_gearing_none_when_absent():
    bike = load_bike("nogears", profile=PROFILE)
    assert bike.gearing is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_bike_config_gearing.py -v`
Expected: FAIL — `TypeError` (unexpected `gearing`) or `AttributeError: 'BikeConfig' object has no attribute 'gearing'`.

- [ ] **Step 3: Add the field and parse it**

In `scripts/bike_config.py`, add to the `BikeConfig` dataclass after line 59 (`unvalidated_by_model_source`):

```python
    gearing: Optional[dict] = None
```

In `load_bike`, add before the `return BikeConfig(` (after line 113):

```python
    gearing = raw.get("gearing") or None
```

And add to the `BikeConfig(...)` constructor call (after the `unvalidated_by_model_source=` line):

```python
        gearing=gearing,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_bike_config_gearing.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing bike_config tests to confirm no regression**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest tests/test_bike_config.py -v`
Expected: PASS (all existing tests green).

- [ ] **Step 6: Add gearing to the rider's profile (manual, NOT committed)**

In `USER_PROFILE.md`, under `bikes.tripster:` add (inline-list syntax — the frontmatter parser supports flow lists like `surfaces_supported`):

```yaml
    gearing:
      chainrings_t: [30, 39, 50]
      cassette_t: [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]
```

Verify it parses:
Run: `/opt/miniconda3/envs/cycling/bin/python -c "import sys; sys.path.insert(0,'scripts'); from bike_config import load_bike; print(load_bike('tripster').gearing)"`
Expected: `{'chainrings_t': [30, 39, 50], 'cassette_t': [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]}`

If it prints `None`, the frontmatter parser did not handle the nested block — fall back to a single inline mapping:
`gearing: {chainrings_t: [30, 39, 50], cassette_t: [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]}`

- [ ] **Step 7: Commit**

```bash
git add scripts/bike_config.py scripts/_tests/test_bike_config_gearing.py
git commit -m "Add optional gearing field to BikeConfig"
```

---

## Task 2: Create `scripts/gearing.py`

**Files:**
- Create: `scripts/gearing.py`
- Test: `scripts/_tests/test_gearing.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_tests/test_gearing.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gearing import cadence_rpm, suggest_gear


class FakeBike:
    wheel_circ_m = 2.155
    gearing = {
        "chainrings_t": [30, 39, 50],
        "cassette_t": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32],
    }


class NoGearBike:
    wheel_circ_m = 1.59
    gearing = None


def test_cadence_rpm_known_value():
    # 30T x 15T on 2.155m wheel = development 4.31 m/rev.
    # At 15 km/h = 250 m/min -> 250/4.31 = ~58 rpm.
    rpm = cadence_rpm(15.0, 30, 15, 2.155)
    assert abs(rpm - 58.0) < 1.0


def test_cadence_rpm_zero_speed():
    assert cadence_rpm(0.0, 30, 15, 2.155) == 0.0


def test_suggest_gear_targets_prefer_rpm():
    # At 12 km/h (a ~10% climb speed), prefer 70 rpm.
    cr, cog, rpm = suggest_gear(12.0, FakeBike(), prefer_rpm=70.0)
    assert (cr, cog) in {(c, k) for c in FakeBike.gearing["chainrings_t"]
                         for k in FakeBike.gearing["cassette_t"]}
    assert 60 <= rpm <= 80  # close to 70


def test_suggest_gear_none_without_gearing():
    assert suggest_gear(20.0, NoGearBike()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_gearing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gearing'`.

- [ ] **Step 3: Write the implementation**

Create `scripts/gearing.py`:

```python
"""Cadence and gear-selection maths for derailleur bikes.

Pure functions: no I/O, no profile loading. Used by analyse_gpx (and later
analyse_climbs) to suggest a gear + cadence for a target speed on a climb.
"""
from typing import Optional, Tuple


def cadence_rpm(speed_kmh: float, chainring_t: int, cog_t: int,
                wheel_circ_m: float) -> float:
    """Pedal cadence (rpm) to hold speed_kmh in the given gear.

    development (m per crank rev) = wheel_circ_m * chainring_t / cog_t
    rpm = (speed in m/min) / development
    """
    if speed_kmh <= 0 or chainring_t <= 0 or cog_t <= 0 or wheel_circ_m <= 0:
        return 0.0
    speed_m_min = speed_kmh * 1000.0 / 60.0
    development_m = wheel_circ_m * chainring_t / cog_t
    return speed_m_min / development_m


def suggest_gear(speed_kmh: float, bike, prefer_rpm: float = 70.0
                 ) -> Optional[Tuple[int, int, float]]:
    """Pick (chainring_t, cog_t, rpm) whose cadence is closest to prefer_rpm.

    Prefers gears giving a plausible cadence (50-110 rpm); if none qualify,
    returns the overall closest. Returns None if the bike has no gearing.
    """
    gearing = getattr(bike, "gearing", None)
    if not gearing:
        return None
    chainrings = gearing["chainrings_t"]
    cogs = gearing["cassette_t"]

    in_range = []
    all_combos = []
    for cr in chainrings:
        for cog in cogs:
            rpm = cadence_rpm(speed_kmh, cr, cog, bike.wheel_circ_m)
            err = abs(rpm - prefer_rpm)
            all_combos.append((err, cr, cog, rpm))
            if 50.0 <= rpm <= 110.0:
                in_range.append((err, cr, cog, rpm))

    pool = in_range if in_range else all_combos
    if not pool:
        return None
    pool.sort(key=lambda t: t[0])
    _err, cr, cog, rpm = pool[0]
    return (cr, cog, rpm)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_gearing.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/gearing.py scripts/_tests/test_gearing.py
git commit -m "Add gearing.py: cadence_rpm + suggest_gear"
```

---

## Task 3: Create `scripts/climb_categories.py` (move categorise, add gate + selection)

**Files:**
- Create: `scripts/climb_categories.py`
- Modify: `scripts/analyse_climbs.py` (remove `CATEGORIES`/`categorise` defs, import them)
- Test: `scripts/_tests/test_climb_categories.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_tests/test_climb_categories.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from climb_categories import categorise, is_significant, select_climbs_for_detail


class FakeVer:
    def __init__(self, peak_25m=None, walls=None):
        self.mean_max = {"peak_25m": peak_25m}
        self.walls = walls or []


def climb(length_m, avg, mx=0.0):
    return {"length_m": length_m, "avg_grad_pct": avg, "max_grad_pct": mx,
            "start_km": 0.0, "end_km": length_m / 1000.0}


def test_categorise_cat3_threshold():
    # 2km x 4% = index 8 -> Cat 3
    name, pts, _b, _f, index = categorise(2.0, 4.0)
    assert name == "Cat 3"
    assert abs(index - 8.0) < 1e-6


def test_significant_cat3_by_index():
    ok, reason = is_significant(climb(2000, 4.0))
    assert ok and "Cat 3" in reason


def test_significant_short_steep_by_peak25():
    # Richmond climb 2: 580m x 4.9% (index 2.8, Cat 4) but peak-25m 10.6%
    ok, reason = is_significant(climb(580, 4.9), FakeVer(peak_25m=10.6))
    assert ok and "peak-25m" in reason


def test_significant_by_wall():
    ok, reason = is_significant(climb(580, 4.9),
                                FakeVer(peak_25m=6.0, walls=[{"length_m": 40}]))
    assert ok and "wall" in reason


def test_not_significant_gentle_drag():
    ok, _ = is_significant(climb(1100, 2.2, mx=4.2), FakeVer(peak_25m=4.4))
    assert not ok


def test_significant_lofi_fallback_uses_gpx_max():
    # No verification: fall back to GPX max_grad_pct >= 8
    ok, reason = is_significant(climb(580, 4.9, mx=9.9), verification=None)
    assert ok and "GPX" in reason


def test_select_cat3_never_capped():
    climbs = [climb(2000, 4.0)] * 3  # three Cat 3 (index 8)
    # tiny cap, but Cat3 must all survive
    idx = select_climbs_for_detail(climbs, mode="auto", cap=1)
    assert idx == [0, 1, 2]


def test_select_caps_minor_climbs():
    # one Cat3 + three short-steep minors; cap minors at 2
    climbs = [climb(2000, 4.0), climb(300, 5.0), climb(300, 5.0), climb(300, 5.0)]
    vers = [FakeVer(), FakeVer(peak_25m=12.0), FakeVer(peak_25m=10.0),
            FakeVer(peak_25m=9.0)]
    idx = select_climbs_for_detail(climbs, vers, mode="auto", cap=2)
    # Cat3 (0) always in; top-2 minors by peak25 are indices 1 and 2
    assert idx == [0, 1, 2]


def test_select_mode_all_and_none():
    climbs = [climb(2000, 4.0), climb(300, 2.0)]
    assert select_climbs_for_detail(climbs, mode="all") == [0, 1]
    assert select_climbs_for_detail(climbs, mode="none") == []


def test_select_mode_explicit_indices():
    climbs = [climb(2000, 4.0), climb(300, 2.0), climb(400, 3.0)]
    assert select_climbs_for_detail(climbs, mode=[1, 3]) == [0, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_climb_categories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'climb_categories'`.

- [ ] **Step 3: Create the module**

Create `scripts/climb_categories.py`. Copy `CATEGORIES` and `categorise` **verbatim** from `analyse_climbs.py` (current lines 41-46 and 63-69), then add the gate and selection helpers:

```python
"""Climb categorisation (UCI-style) + significance gate + detail selection.

Shared by analyse_climbs.py (FIT) and analyse_gpx.py (GPX). Categorisation
moved here verbatim from analyse_climbs.py so both tools agree.
"""
from typing import Optional

# (lower_index_inclusive, name, points, badge_colour, fill_colour)
CATEGORIES = [
    (40, 'Cat 1', 10, '#cc4400', '#ff7700'),
    (16, 'Cat 2',  5, '#cc9900', '#ffcc33'),
    ( 6, 'Cat 3',  2, '#0066cc', '#3399ff'),
    ( 2, 'Cat 4',  1, '#006633', '#33aa66'),
]

# Highest threshold first not needed; mirror analyse_climbs ordering exactly.
CATEGORIES_ORDERED = sorted(CATEGORIES, key=lambda c: c[0], reverse=True)


def categorise(length_km, avg_grade_pct):
    """Return (category_name, kom_points, badge_colour, fill_colour, index)."""
    index = length_km * avg_grade_pct
    for threshold, name, points, badge, fill in CATEGORIES_ORDERED:
        if index >= threshold:
            return name, points, badge, fill, index
    return 'uncat', 0, '#888888', '#cccccc', index


STEEP_PEAK25_PCT = 8.0  # short-pitch gate threshold


def _peak25(verification, climb):
    """Best available peak-25m grade for ranking; falls back to GPX max."""
    if verification is not None:
        mm = getattr(verification, "mean_max", None) or {}
        p = mm.get("peak_25m")
        if p is not None:
            return p
    return float(climb.get("max_grad_pct", 0.0))


def is_significant(climb, verification=None):
    """Return (bool, reason). A climb earns a detail chart if Cat 3+ OR has a
    wall OR has a steep short pitch (peak-25m >= 8%). Without verification,
    fall back to Cat 3+ or GPX max_grad_pct >= 8%."""
    _n, _p, _b, _f, index = categorise(climb["length_m"] / 1000.0,
                                       climb["avg_grad_pct"])
    if index >= 6:
        return True, f"Cat 3+ (index {index:.1f})"
    if verification is not None:
        if getattr(verification, "walls", None):
            return True, "wall >=10% sustained >=30m"
        mm = getattr(verification, "mean_max", None) or {}
        p25 = mm.get("peak_25m")
        if p25 is not None and p25 >= STEEP_PEAK25_PCT:
            return True, f"steep pitch (peak-25m {p25:.1f}%)"
        return False, ""
    if float(climb.get("max_grad_pct", 0.0)) >= STEEP_PEAK25_PCT:
        return True, f"steep pitch (GPX max {climb['max_grad_pct']:.1f}%)"
    return False, ""


def select_climbs_for_detail(climbs, verifications=None, mode="auto", cap=8):
    """Return sorted list of 0-based climb indices to render detail for.

    mode: 'auto' (gate + cap), 'all', 'none', or a list of 1-based indices.
    Cat 3+ climbs are NEVER dropped by the cap; the cap bounds only the
    sub-Cat-3 climbs that qualified via wall / peak-25m, keeping the hardest.
    """
    n = len(climbs)
    if mode == "none":
        return []
    if mode == "all":
        return list(range(n))
    if isinstance(mode, (list, tuple)):
        return sorted(i - 1 for i in mode if 1 <= i <= n)

    vers = list(verifications) if verifications else [None] * n
    if len(vers) < n:
        vers += [None] * (n - len(vers))

    cat3 = []
    minor = []  # (peak25, index_in_climbs)
    for i, c in enumerate(climbs):
        ok, _reason = is_significant(c, vers[i])
        if not ok:
            continue
        _n, _p, _b, _f, index = categorise(c["length_m"] / 1000.0,
                                           c["avg_grad_pct"])
        if index >= 6:
            cat3.append(i)
        else:
            minor.append((_peak25(vers[i], c), i))

    minor.sort(key=lambda t: t[0], reverse=True)
    minor_idx = [i for _p25, i in minor[:max(0, cap)]]
    return sorted(cat3 + minor_idx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_climb_categories.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Point analyse_climbs.py at the shared module**

In `scripts/analyse_climbs.py`, DELETE the local `CATEGORIES` list (lines ~41-46) and the `categorise` function (lines ~63-69). Add an import near the top (after the existing imports):

```python
from climb_categories import CATEGORIES, categorise
```

- [ ] **Step 6: Confirm analyse_climbs still imports and its categorise is identical**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "import sys; sys.path.insert(0,'scripts'); import analyse_climbs as a; print(a.categorise(1.45, 9.0))"`
Expected: `('Cat 3', 2, '#0066cc', '#3399ff', 13.05)` (high Cat 3 benchmark — same as before the move).

- [ ] **Step 7: Commit**

```bash
git add scripts/climb_categories.py scripts/analyse_climbs.py scripts/_tests/test_climb_categories.py
git commit -m "Extract climb categorisation + add significance gate & selection"
```

---

## Task 4: Create `scripts/chart_climb_detail.py` (move the renderer)

**Files:**
- Create: `scripts/chart_climb_detail.py`
- Modify: `scripts/analyse_climbs.py` (remove moved functions, import them)
- Test: `scripts/_tests/test_chart_climb_detail.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_tests/test_chart_climb_detail.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from chart_climb_detail import plot_climb_detail


def test_plot_climb_detail_writes_png(tmp_path):
    # 600m climb: flat 0-200m, then ~10% to the top.
    d = np.arange(0, 600, 10, dtype=float)
    alt = np.where(d < 200, 20.0, 20.0 + (d - 200) * 0.10)
    arrays = {"distance_m": d, "altitude_m": alt}
    climb = {"start_km": 0.0, "end_km": 0.6, "length_m": 600.0,
             "avg_grad_pct": 6.6, "max_grad_pct": 10.0}
    out = tmp_path / "climb1.png"
    ok = plot_climb_detail(arrays, climb, 1, out)
    assert ok is True
    assert out.exists() and out.stat().st_size > 1000


def test_plot_climb_detail_too_short_returns_false(tmp_path):
    d = np.array([0.0, 10.0])
    alt = np.array([20.0, 21.0])
    arrays = {"distance_m": d, "altitude_m": alt}
    climb = {"start_km": 0.0, "end_km": 0.01, "length_m": 10.0,
             "avg_grad_pct": 10.0, "max_grad_pct": 10.0}
    out = tmp_path / "climb_short.png"
    assert plot_climb_detail(arrays, climb, 1, out) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_chart_climb_detail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'chart_climb_detail'`.

- [ ] **Step 3: Create the module by moving the renderer**

Create `scripts/chart_climb_detail.py` with a non-interactive matplotlib backend, then move these functions **verbatim** from `analyse_climbs.py`: `grade_colour` (lines ~72-79), `climb_stats` (lines ~81-107), `resample_segment` (lines ~109-129), `plot_climb_detail` (lines ~132-243). Header:

```python
"""Per-climb TdF-style detail chart. Shared renderer for analyse_climbs (FIT)
and analyse_gpx (GPX). Moved verbatim from analyse_climbs.py."""
import matplotlib
matplotlib.use("Agg")  # headless; safe for tests and CLI
import matplotlib.pyplot as plt
import numpy as np

from climb_categories import categorise

# <-- paste grade_colour, climb_stats, resample_segment, plot_climb_detail here,
#     unchanged from analyse_climbs.py -->
```

Note: `plot_climb_detail` calls `categorise` — it now comes from the import above, not a local def. `resample_segment` and `climb_stats` use only `arrays['distance_m']` / `arrays['altitude_m']` and numpy.

- [ ] **Step 4: Point analyse_climbs.py at the shared renderer**

In `scripts/analyse_climbs.py`, DELETE the now-moved `grade_colour`, `climb_stats`, `resample_segment`, `plot_climb_detail` definitions. Add to the import block:

```python
from chart_climb_detail import (
    grade_colour, climb_stats, resample_segment, plot_climb_detail,
)
```

(If `plot_overview` in `analyse_climbs.py` also uses `grade_colour`/`resample_segment`, the import now supplies them — confirm `plot_overview` still resolves all names.)

- [ ] **Step 5: Run the render tests**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_chart_climb_detail.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Confirm analyse_climbs.py still imports cleanly**

Run: `/opt/miniconda3/envs/cycling/bin/python -c "import sys; sys.path.insert(0,'scripts'); import analyse_climbs; print('ok')"`
Expected: `ok` (no ImportError / NameError).

- [ ] **Step 7: Commit**

```bash
git add scripts/chart_climb_detail.py scripts/analyse_climbs.py scripts/_tests/test_chart_climb_detail.py
git commit -m "Extract per-climb renderer into chart_climb_detail.py"
```

---

## Task 5: Wire selection + per-climb charts into analyse_gpx.py

**Files:**
- Modify: `scripts/analyse_gpx.py` (imports, CLI args, render block after overview ~763, a `match_verification` helper)
- Test: `scripts/_tests/test_analyse_gpx_climb_detail.py`

- [ ] **Step 1: Write the failing test (lo-fi, hermetic — no DEM/network)**

Create `scripts/_tests/test_analyse_gpx_climb_detail.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subprocess


def _write_gpx(path):
    # ~1.2km: 600m flat then 600m climbing ~10% (steep -> qualifies lo-fi).
    pts = []
    lat = 51.0
    ele = 20.0
    for i in range(120):
        lat += 0.00009  # ~10m per step
        if i >= 60:
            ele += 1.0   # +1m per 10m = 10%
        pts.append(f'<trkpt lat="{lat:.6f}" lon="-0.1"><ele>{ele:.1f}</ele></trkpt>')
    path.write_text(
        '<?xml version="1.0"?><gpx version="1.1"><trk><trkseg>'
        + "".join(pts) + "</trkseg></trk></gpx>")


def test_climb_detail_chart_generated_lofi(tmp_path):
    gpx = tmp_path / "steeptest.gpx"
    _write_gpx(gpx)
    charts = tmp_path / "charts"
    charts.mkdir()
    # Run analyse_gpx lo-fi (--no-verify), forcing all detected climbs.
    cmd = [
        "/opt/miniconda3/envs/cycling/bin/python", "scripts/analyse_gpx.py",
        "--bike", "tripster", "--surface", "tarmac", "--save", "--no-verify",
        "--climb-detail", "all", "--chart-dir", str(charts), str(gpx),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert res.returncode == 0, res.stderr
    pngs = list(charts.glob("steeptest-climb*.png"))
    assert pngs, f"no per-climb png; stderr={res.stderr}"
```

NOTE: this test assumes a `--chart-dir` arg so output lands in `tmp_path`. If you prefer not to add `--chart-dir`, drop that arg and assert on `rides/charts/steeptest-climb*.png`, then clean up — but `--chart-dir` keeps the test hermetic and is the cleaner choice; add it in Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_analyse_gpx_climb_detail.py -v`
Expected: FAIL — unrecognized `--climb-detail` / `--chart-dir` argument (returncode != 0).

- [ ] **Step 3: Add CLI args**

In `scripts/analyse_gpx.py` `main()` argparse block (near the other `parser.add_argument` calls, ~lines 554-572), add:

```python
    parser.add_argument('--climb-detail', default='auto',
                        help="Per-climb detail charts: 'auto' (significance "
                             "gate), 'all', 'none', or comma indices e.g. 1,3")
    parser.add_argument('--climb-detail-max', type=int, default=8,
                        help='Cap on sub-Cat-3 detail charts (Cat 3+ never '
                             'capped). Default 8.')
    parser.add_argument('--chart-dir', default='rides/charts',
                        help='Output directory for charts.')
```

- [ ] **Step 4: Add the import and a verification-matcher helper**

Near the top imports of `analyse_gpx.py`:

```python
from climb_categories import select_climbs_for_detail
from chart_climb_detail import plot_climb_detail
```

Add this helper at module level (e.g. after `render_overview_chart`):

```python
def match_verifications(climbs, report):
    """Align each detected climb (dict) to its ClimbVerification by km overlap.
    Returns a list parallel to `climbs`; entries are None when no match."""
    out = []
    cvs = list(getattr(report, 'climbs', []) or []) if report else []
    for c in climbs:
        best = None
        for cv in cvs:
            lo = max(c['start_km'], cv.km_start)
            hi = min(c['end_km'], cv.km_end)
            overlap = max(0.0, hi - lo)
            if overlap > 0 and (best is None or overlap > best[0]):
                best = (overlap, cv)
        out.append(best[1] if best else None)
    return out


def parse_climb_detail_mode(raw):
    """'auto'|'all'|'none' pass through; '1,3' -> [1, 3]."""
    raw = (raw or 'auto').strip()
    if raw in ('auto', 'all', 'none'):
        return raw
    return [int(x) for x in raw.split(',') if x.strip()]
```

- [ ] **Step 5: Render per-climb charts after the overview block**

In `main()`, immediately after the overview chart is saved (after the `[Saved {data_source} chart ...]` print, ~line 763), add. Use the already-computed `report`, `use_hifi`, and `result`/`r` climbs list (the dict returned by `analyse()`; the climbs list is `result['climbs']`):

```python
                # Per-climb detail charts (additive; never hard-fail the run).
                try:
                    climbs = result.get('climbs', [])
                    if climbs:
                        mode = parse_climb_detail_mode(args.climb_detail)
                        vers = match_verifications(climbs, report)
                        chosen = select_climbs_for_detail(
                            climbs, vers, mode=mode, cap=args.climb_detail_max)
                        # Build arrays: hi-fi stitched profile if available, else GPX.
                        if use_hifi and getattr(report, 'stitched_dists', None):
                            arrays = {
                                'distance_m': np.asarray(report.stitched_dists, float),
                                'altitude_m': np.asarray(report.stitched_elevs, float),
                            }
                        else:
                            parsed_g = parse_gpx(f)
                            arrays = {
                                'distance_m': np.asarray(parsed_g['dists'], float),
                                'altitude_m': np.asarray(parsed_g['elevs'], float),
                            }
                        chart_dir = Path(args.chart_dir)
                        chart_dir.mkdir(parents=True, exist_ok=True)
                        stem = Path(f).stem
                        for idx in chosen:
                            out_png = chart_dir / f'{stem}-climb{idx + 1}.png'
                            if plot_climb_detail(arrays, climbs[idx], idx + 1, out_png):
                                print(f'[Saved per-climb chart {out_png}]')
                except Exception as e:
                    print(f'⚠ per-climb detail skipped: {e}', file=sys.stderr)
```

CONFIRM during implementation: the exact name of the parsed-GPX distance/elevation keys. `match_verifications`/overview use `parse_gpx(f)`; check whether it returns `'dists'`/`'elevs'` or `'distance_m'`/`'altitude_m'` (grep `def parse_gpx` in analyse_gpx.py) and use the real keys. The arrays dict passed to `plot_climb_detail` MUST use keys `distance_m` and `altitude_m` (that is what `resample_segment` reads).

- [ ] **Step 6: Run the integration test**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_analyse_gpx_climb_detail.py -v`
Expected: PASS — `steeptest-climb1.png` created in the tmp chart dir.

- [ ] **Step 7: Manual smoke on the real Richmond route (hi-fi path)**

Run:
```bash
/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py --bike tripster \
  --surface tarmac --save --coverage-gap api \
  "routes/2026-05-23_2977749267_Richmond lap and back.gpx"
```
Expected: console shows `[Saved per-climb chart ...climb1.png]` and `...climb2.png` (both qualify via the gate); files exist in `rides/charts/`.

- [ ] **Step 8: Commit**

```bash
git add scripts/analyse_gpx.py scripts/_tests/test_analyse_gpx_climb_detail.py
git commit -m "Wire per-climb detail charts into analyse_gpx (gate + cap + flag)"
```

---

## Task 6: Add gear + rpm to the per-climb pacing in the markdown

**Files:**
- Modify: `scripts/analyse_gpx.py` (the per-climb pacing block, ~lines 454-470, and `predict_climb` ~208-265 if speeds aren't already on the climb dict)
- Test: `scripts/_tests/test_analyse_gpx_climb_detail.py` (extend)

- [ ] **Step 1: Write the failing test (extend the integration test)**

Append to `scripts/_tests/test_analyse_gpx_climb_detail.py`:

```python
def test_pacing_has_gear_and_rpm(tmp_path):
    gpx = tmp_path / "steeptest2.gpx"
    _write_gpx(gpx)
    charts = tmp_path / "charts2"
    charts.mkdir()
    cmd = [
        "/opt/miniconda3/envs/cycling/bin/python", "scripts/analyse_gpx.py",
        "--bike", "tripster", "--surface", "tarmac", "--save", "--no-verify",
        "--climb-detail", "all", "--chart-dir", str(charts), str(gpx),
    ]
    root = Path(__file__).resolve().parents[2]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    assert res.returncode == 0, res.stderr
    md = (root / "routes" / "steeptest2-prediction.md").read_text()
    # Expect a gear like "30x28" and an "rpm" mention in the per-climb pacing.
    assert "rpm" in md.lower()
    assert "x" in md  # gear notation chainring x cog
    (root / "routes" / "steeptest2-prediction.md").unlink()  # cleanup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_analyse_gpx_climb_detail.py::test_pacing_has_gear_and_rpm -v`
Expected: FAIL — no `rpm` in the markdown.

- [ ] **Step 3: Emit gear + rpm in the per-climb pacing lines**

In `analyse_gpx.py`, locate where the per-climb GPX-PACING block writes the FTP/MAP/Z3 speed lines (`### Climb N` section, ~lines 456-470). For each speed line, compute and append gear + cadence. Add the import (if not already present from Task 5):

```python
from gearing import suggest_gear
```

For a given climb speed `spd_kmh` and the resolved `bike`, build a suffix:

```python
def _gear_suffix(spd_kmh, bike, prefer_rpm=70.0):
    g = suggest_gear(spd_kmh, bike, prefer_rpm=prefer_rpm)
    if not g:
        return ""
    cr, cog, rpm = g
    return f" — gear {cr}x{cog} @ {rpm:.0f} rpm"
```

Append `_gear_suffix(ftp_speed, bike)` to the `Speed @ FTP` line, `_gear_suffix(map_speed, bike)` to the `Speed @ MAP` line, and the Z3 line. (These speed values are already computed in the pacing block / `predict_climb`; reuse the existing variables rather than recomputing.) Bikes without gearing yield an empty suffix — line unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/test_analyse_gpx_climb_detail.py -v`
Expected: PASS (both integration tests green).

- [ ] **Step 5: Commit**

```bash
git add scripts/analyse_gpx.py scripts/_tests/test_analyse_gpx_climb_detail.py
git commit -m "Add suggested gear + cadence to per-climb pacing"
```

---

## Task 7: Cleanup + full regression

**Files:**
- Delete: `scripts/_per_climb_detail.py`

- [ ] **Step 1: Delete the throwaway one-off**

```bash
git rm scripts/_per_climb_detail.py 2>/dev/null || rm -f scripts/_per_climb_detail.py
```
(It was untracked; `rm -f` suffices.)

- [ ] **Step 2: Run the full test suite**

Run: `/opt/miniconda3/envs/cycling/bin/python -m pytest scripts/_tests/ tests/ -v`
Expected: PASS — all tests green, including the pre-existing `test_verify_climbs.py`, `test_bike_config.py`, etc.

- [ ] **Step 3: Final manual verification on Richmond (the original ask)**

Run:
```bash
/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py --bike tripster \
  --surface tarmac --save --coverage-gap api \
  "routes/2026-05-23_2977749267_Richmond lap and back.gpx"
```
Confirm: overview chart + `...climb1.png` + `...climb2.png` exist; prediction MD references them and shows gear/rpm in per-climb pacing; Fidelity Report still embedded.

- [ ] **Step 4: Commit**

```bash
git add -A scripts/
git commit -m "Remove one-off per-climb script; feature complete"
```

---

## Self-Review notes (author)

- **Spec coverage:** significance gate (Task 3), Cat-3-never-capped (Task 3 `select_climbs_for_detail` + test), `all`/`none`/indices flag (Task 5), shared-module extraction B (Tasks 3-4), gearing config (Task 1), cadence+gear column (Task 6), hi-fi/lo-fi arrays + offline (Task 5 Step 5), reuse existing renderer (Task 4), cleanup (Task 7). Workout-zone-aware explicitly deferred — not in plan, matches spec non-goals.
- **Open confirmations flagged inline (cheap greps at implementation time):** exact `parse_gpx` return keys (Task 5 Step 5); whether `analyse_climbs.plot_overview` shares `grade_colour`/`resample_segment` (Task 4 Step 4); whether frontmatter parser accepts the nested `gearing:` block vs inline mapping (Task 1 Step 6).
- **Type consistency:** climb dict keys (`start_km/end_km/length_m/avg_grad_pct/max_grad_pct`) match `analyse_gpx.py:194-200`. `ClimbVerification` exposes `km_start/km_end/walls/mean_max` (verify_climbs.py:20-46). `arrays` keys `distance_m`/`altitude_m` match `resample_segment` (analyse_climbs.py:111-112). `suggest_gear` returns `(cr, cog, rpm)` consistently in Tasks 2/5/6.
