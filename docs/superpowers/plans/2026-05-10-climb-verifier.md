# Climb Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-tier climb-verification layer that detects gradient underestimation and missed climbs in routing-engine GPX files by re-sampling against on-device 1m lidar (DEFRA UK / IGN France) with GPXZ free-tier API as fallback.

**Architecture:** A new `verify_climbs.py` orchestrator runs after the existing `analyse_gpx.py` baseline. It samples each candidate climb at ≤5m spacing against local DEM tiles via `local_dem.py` (rasterio-backed), falls back to the GPXZ API via `elevation_fallback.py` for coords outside loaded tiles, and emits an inlined Fidelity Report into `-prediction.md`. A `fetch_dem_tiles.py` helper bulk-downloads tiles by bbox/region.

**Tech Stack:** Python 3.11+, conda, rasterio (DEM I/O), pyproj (CRS transforms), numpy, requests (GPXZ + tile fetch), pytest.

---

## Pre-flight: spec context

Read `docs/superpowers/specs/2026-05-10-climb-verifier-design.md` before starting. Tasks below assume that spec as canonical.

Reference data for end-to-end regression:
- Planned GPX: `routes/2026-05-09-sample-route.gpx`
- Actual FIT: `rides/2026-05-09-094746-ELEMNT_ROAM_66CD-85-0.fit`
- Expected verifier output: flag C2/C3/C8 (km 6.75 / 12.85 / 47.95) as underestimated by >5pp; detect missed climb at km 46.55 (650m, peak 10.9%).

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `environment.yml` | Create | Portable conda env, top-level loose pins, no build hashes |
| `.gitignore` | Modify | Ignore `environment_*.yml` diagnostics, `~/.config/cycling-coach/` is outside repo so n/a |
| `scripts/local_dem.py` | Create | rasterio-backed tile loader + bilinear sampler |
| `scripts/elevation_fallback.py` | Create | GPXZ free-tier API client |
| `scripts/fetch_dem_tiles.py` | Create | Bulk DEM tile downloader (bbox / GPX / region) |
| `scripts/verify_climbs.py` | Create | Orchestrator: re-sample climbs, detect missed climbs, write Fidelity Report |
| `scripts/analyse_gpx.py` | Modify | Default-on `--verify`, `--no-verify` to opt out, embed Fidelity Report inline |
| `scripts/_tests/` | Create | pytest tests (named with underscore to not clash with rider's gitignored `tests/`) |
| `scripts/_tests/conftest.py` | Create | Shared fixtures (synthetic GPX, fake DEM) |
| `scripts/_tests/test_local_dem.py` | Create | local_dem unit tests |
| `scripts/_tests/test_elevation_fallback.py` | Create | GPXZ client tests (mocked HTTP) |
| `scripts/_tests/test_verify_climbs.py` | Create | orchestrator unit + sample-route regression |
| `scripts/_tests/test_fetch_dem_tiles.py` | Create | fetcher tests (mocked HTTP) |

---

## Task 1: Portable conda environment file

**Files:**
- Create: `environment.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Write `environment.yml`**

```yaml
name: cycling
channels:
  - conda-forge
dependencies:
  - python>=3.11
  - numpy>=1.26
  - scipy>=1.11
  - matplotlib>=3.8
  - adjusttext>=1.0
  - fitparse>=1.2
  - rasterio>=1.3
  - pyproj>=3.6
  - requests>=2.31
  - pytest>=7.4
  - pip
```

- [ ] **Step 2: Add diagnostic-snapshot ignore to `.gitignore`**

Append at end of `.gitignore`:

```
# Conda diagnostic snapshots — not the canonical env file
environment_cleaned.yml
environment_export.yml
```

- [ ] **Step 3: Verify resolves on osx-arm64**

Run: `conda env create -n cycling-test -f environment.yml --dry-run`
Expected: solver succeeds, prints package list. Tear down: `conda env remove -n cycling-test --yes` (the dry-run shouldn't have created it, but be safe).

- [ ] **Step 4: Commit**

```bash
git add environment.yml .gitignore
git commit -m "Add portable conda environment.yml (top-level pins only)"
```

---

## Task 2: `local_dem.py` — DEM tile loader & sampler

**Files:**
- Create: `scripts/local_dem.py`
- Create: `scripts/_tests/conftest.py`
- Create: `scripts/_tests/test_local_dem.py`

**Public API:**
```python
class LocalDEM:
    def __init__(self, root: Path): ...
    def covers(self, lat: float, lon: float) -> bool: ...
    def sample(self, lat: float, lon: float) -> Optional[float]: ...
    def sample_polyline(self, coords: list[tuple[float, float]], stride_m: float) -> list[Optional[float]]: ...
```

`root` is a directory containing GeoTIFF tiles. The class lazily opens tiles with rasterio, transforms input WGS84 lat/lon to each tile's CRS via pyproj, bilinearly samples, returns metres-above-sea-level. Returns `None` if no tile covers a point.

- [ ] **Step 1: Create test fixture (synthetic GeoTIFF)**

Write `scripts/_tests/conftest.py`:

```python
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from pathlib import Path


@pytest.fixture
def synthetic_dem_dir(tmp_path):
    """A tiny 100x100 GeoTIFF over a known WGS84 bbox with a synthetic ramp.

    bbox: lon 0.0..0.001, lat 51.0..51.001 (~111m x 111m tile).
    Elevation: linear ramp from 100m (south) to 200m (north).
    """
    arr = np.tile(np.linspace(200, 100, 100, dtype=np.float32).reshape(-1, 1), (1, 100))
    transform = from_origin(0.0, 51.001, 0.00001, 0.00001)  # 1px ≈ 1m
    out = tmp_path / "synthetic.tif"
    with rasterio.open(
        out, "w", driver="GTiff", height=100, width=100, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(arr, 1)
    return tmp_path
```

- [ ] **Step 2: Write failing tests**

Write `scripts/_tests/test_local_dem.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from local_dem import LocalDEM


def test_sample_inside_tile_returns_float(synthetic_dem_dir):
    dem = LocalDEM(synthetic_dem_dir)
    val = dem.sample(51.0005, 0.0005)
    assert val is not None
    assert 100.0 <= val <= 200.0


def test_sample_outside_tile_returns_none(synthetic_dem_dir):
    dem = LocalDEM(synthetic_dem_dir)
    assert dem.sample(48.0, 2.0) is None


def test_covers(synthetic_dem_dir):
    dem = LocalDEM(synthetic_dem_dir)
    assert dem.covers(51.0005, 0.0005) is True
    assert dem.covers(48.0, 2.0) is False


def test_sample_polyline_mixed_coverage(synthetic_dem_dir):
    dem = LocalDEM(synthetic_dem_dir)
    coords = [(51.0005, 0.0005), (48.0, 2.0)]
    out = dem.sample_polyline(coords, stride_m=10.0)
    assert out[0] is not None
    assert out[-1] is None


def test_ramp_gradient_is_correct(synthetic_dem_dir):
    """Synthetic ramp: south→north, 100m→200m over ~111m. Grade ≈ 90%.
    We test the relative direction, not the exact value (CRS rounding)."""
    dem = LocalDEM(synthetic_dem_dir)
    south = dem.sample(51.0001, 0.0005)
    north = dem.sample(51.0009, 0.0005)
    assert north > south
```

Run: `pytest scripts/_tests/test_local_dem.py -v`
Expected: ImportError / module not found.

- [ ] **Step 3: Implement `local_dem.py`**

```python
"""On-device DEM tile loader and sampler.

Loads a directory of GeoTIFF tiles, transforms WGS84 lat/lon queries to each
tile's CRS, and bilinearly samples the elevation. Returns None for points not
covered by any loaded tile, so callers can fall back to an API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from pyproj import Transformer


class LocalDEM:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._tiles = []  # list of (rasterio.DatasetReader, Transformer-from-wgs84)
        for tif in sorted(self.root.rglob("*.tif")):
            ds = rasterio.open(tif)
            tr = Transformer.from_crs("EPSG:4326", ds.crs, always_xy=True)
            self._tiles.append((ds, tr))

    def _find_tile(self, lat: float, lon: float):
        for ds, tr in self._tiles:
            x, y = tr.transform(lon, lat)
            left, bottom, right, top = ds.bounds
            if left <= x <= right and bottom <= y <= top:
                return ds, x, y
        return None

    def covers(self, lat: float, lon: float) -> bool:
        return self._find_tile(lat, lon) is not None

    def sample(self, lat: float, lon: float) -> Optional[float]:
        hit = self._find_tile(lat, lon)
        if hit is None:
            return None
        ds, x, y = hit
        # rasterio.sample is nearest-neighbour; do bilinear by reading 2x2.
        row, col = ds.index(x, y, op=float)
        r0, c0 = int(np.floor(row)), int(np.floor(col))
        r1, c1 = r0 + 1, c0 + 1
        if r0 < 0 or c0 < 0 or r1 >= ds.height or c1 >= ds.width:
            # Edge — fall back to nearest valid pixel.
            r0 = max(0, min(ds.height - 1, r0))
            c0 = max(0, min(ds.width - 1, c0))
            val = ds.read(1, window=((r0, r0 + 1), (c0, c0 + 1)))[0, 0]
            return float(val) if not np.isnan(val) else None
        win = ds.read(1, window=((r0, r1 + 1), (c0, c1 + 1)))
        if np.isnan(win).any():
            valid = win[~np.isnan(win)]
            return float(valid.mean()) if valid.size else None
        dr, dc = row - r0, col - c0
        v = (
            win[0, 0] * (1 - dr) * (1 - dc)
            + win[0, 1] * (1 - dr) * dc
            + win[1, 0] * dr * (1 - dc)
            + win[1, 1] * dr * dc
        )
        return float(v)

    def sample_polyline(
        self, coords: list[tuple[float, float]], stride_m: float
    ) -> list[Optional[float]]:
        # Sample at the input coords; densification at stride_m is the caller's
        # job (verify_climbs.py already densifies).
        return [self.sample(lat, lon) for lat, lon in coords]
```

- [ ] **Step 4: Run tests, expect green**

Run: `pytest scripts/_tests/test_local_dem.py -v`
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/local_dem.py scripts/_tests/conftest.py scripts/_tests/test_local_dem.py
git commit -m "Add LocalDEM rasterio-backed tile loader with bilinear sampling"
```

---

## Task 3: `elevation_fallback.py` — GPXZ free-tier client

**Files:**
- Create: `scripts/elevation_fallback.py`
- Create: `scripts/_tests/test_elevation_fallback.py`

**Public API:**
```python
class GPXZClient:
    def __init__(self, key_path: Path = Path.home()/".config/cycling-coach/gpxz.key"): ...
    @property
    def configured(self) -> bool: ...
    def sample_polyline(self, coords: list[tuple[float, float]]) -> list[float]: ...
```

Reads API key from `~/.config/cycling-coach/gpxz.key` (one line). If absent, `configured` is False and `sample_polyline` raises `RuntimeError`. Batches up to 512 points per POST, throttles to 1 rps.

- [ ] **Step 1: Write failing tests**

Write `scripts/_tests/test_elevation_fallback.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from elevation_fallback import GPXZClient


def test_no_key_means_unconfigured(tmp_path):
    c = GPXZClient(key_path=tmp_path / "missing.key")
    assert c.configured is False


def test_with_key_is_configured(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key\n")
    c = GPXZClient(key_path=k)
    assert c.configured is True


def test_unconfigured_raises_on_sample(tmp_path):
    c = GPXZClient(key_path=tmp_path / "missing.key")
    import pytest
    with pytest.raises(RuntimeError):
        c.sample_polyline([(51.5, -0.1)])


def test_sample_polyline_batches_and_returns_floats(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key")
    c = GPXZClient(key_path=k)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "results": [{"elevation": 100.0}, {"elevation": 110.0}]
    }

    with patch("elevation_fallback.requests.post", return_value=fake_resp) as post:
        out = c.sample_polyline([(51.5, -0.1), (51.6, -0.1)])

    assert out == [100.0, 110.0]
    assert post.call_count == 1


def test_sample_polyline_chunks_over_512(tmp_path):
    k = tmp_path / "gpxz.key"
    k.write_text("test-api-key")
    c = GPXZClient(key_path=k)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "results": [{"elevation": 1.0}] * 512
    }

    coords = [(51.5, -0.1)] * 1024
    with patch("elevation_fallback.requests.post", return_value=fake_resp) as post, \
         patch("elevation_fallback.time.sleep"):
        out = c.sample_polyline(coords)

    assert len(out) == 1024
    assert post.call_count == 2
```

Run: `pytest scripts/_tests/test_elevation_fallback.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `elevation_fallback.py`**

```python
"""GPXZ.io free-tier elevation API fallback client.

Used only when the local DEM doesn't cover a route segment. Free tier:
100 requests/day, 1 rps, up to 512 points per POST. Personal use qualifies
for the non-commercial evaluation tier.

API key file: ~/.config/cycling-coach/gpxz.key (one line, plain text).
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

API_URL = "https://api.gpxz.io/v1/elevation/points"
BATCH_SIZE = 512
MIN_INTERVAL_S = 1.0


class GPXZClient:
    def __init__(self, key_path: Path | None = None):
        self.key_path = (
            Path(key_path)
            if key_path is not None
            else Path.home() / ".config" / "cycling-coach" / "gpxz.key"
        )
        self._key: str | None = None
        if self.key_path.exists():
            self._key = self.key_path.read_text().strip() or None

    @property
    def configured(self) -> bool:
        return self._key is not None

    def sample_polyline(self, coords: list[tuple[float, float]]) -> list[float]:
        if not self.configured:
            raise RuntimeError(
                f"GPXZ key not configured. Place a key in {self.key_path}."
            )
        out: list[float] = []
        last_call = 0.0
        for i in range(0, len(coords), BATCH_SIZE):
            chunk = coords[i : i + BATCH_SIZE]
            wait = MIN_INTERVAL_S - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
            payload = {
                "latlons": [{"lat": lat, "lon": lon} for lat, lon in chunk]
            }
            r = requests.post(
                API_URL,
                json=payload,
                headers={"x-api-key": self._key},
                timeout=30,
            )
            last_call = time.monotonic()
            if r.status_code != 200:
                raise RuntimeError(f"GPXZ HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            out.extend(p["elevation"] for p in data["results"])
        return out
```

- [ ] **Step 3: Run tests, expect green**

Run: `pytest scripts/_tests/test_elevation_fallback.py -v`
Expected: all 5 pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/elevation_fallback.py scripts/_tests/test_elevation_fallback.py
git commit -m "Add GPXZ free-tier elevation API fallback client"
```

---

## Task 4: `verify_climbs.py` — orchestrator (core logic)

**Files:**
- Create: `scripts/verify_climbs.py`
- Create: `scripts/_tests/test_verify_climbs.py`

This task implements the orchestrator's pure logic (densification, gradient calc, comparison) without the I/O wiring. CLI and inline-report integration come in Task 6.

**Public API:**
```python
@dataclass
class ClimbVerification:
    name: str
    km_start: float
    km_end: float
    gpx_peak_pct: float
    verified_peak_pct: float
    delta_pp: float
    length_above_8: float
    length_above_10: float
    length_above_12: float
    length_above_14: float
    fallback_used: bool

@dataclass
class FidelityReport:
    route_name: str
    backend: str  # "local-1m" / "gpxz" / "mixed"
    coverage_pct: float
    climbs: list[ClimbVerification]
    missed_climbs: list[dict]  # km, length_m, gain_m, avg_pct, peak_pct
    verdict: str  # "safe" / "minor" / "high"

def verify_route(gpx_path, dem, fallback, climbs_from_gpx) -> FidelityReport: ...
```

- [ ] **Step 1: Write failing tests for densification + gradient calc**

Write `scripts/_tests/test_verify_climbs.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_climbs import (
    densify_polyline,
    longest_run_above,
    classify_verdict,
)


def test_densify_polyline_5m_stride():
    # ~111m segment at the equator
    coords = [(0.0, 0.0), (0.0, 0.001)]
    out = densify_polyline(coords, stride_m=5.0)
    # ~111m / 5m + 1 endpoint
    assert 21 <= len(out) <= 24
    assert out[0] == coords[0]
    assert out[-1] == coords[-1]


def test_longest_run_above_simple():
    # distances every 10m, gradients pattern: [5,9,11,13,12,8,7]
    grades = [5, 9, 11, 13, 12, 8, 7]
    dists = [i * 10.0 for i in range(len(grades))]
    assert longest_run_above(grades, dists, threshold=10) == 30.0  # idx 2..4
    assert longest_run_above(grades, dists, threshold=12) == 20.0  # idx 3..4
    assert longest_run_above(grades, dists, threshold=20) == 0.0


def test_classify_verdict():
    assert classify_verdict(deltas=[0.5, -0.5], missed=0) == "safe"
    assert classify_verdict(deltas=[1.5, 0.0], missed=0) == "minor"
    assert classify_verdict(deltas=[3.0, 0.0], missed=0) == "high"
    assert classify_verdict(deltas=[0.0], missed=1) == "high"
```

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement core helpers**

Write `scripts/verify_climbs.py`:

```python
"""Climb verification orchestrator.

Re-samples each candidate climb from analyse_gpx against a high-fidelity
elevation source (LocalDEM, GPXZ fallback) and produces a Fidelity Report
that flags peak-gradient underestimation and missed climbs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ClimbVerification:
    name: str
    km_start: float
    km_end: float
    gpx_peak_pct: float
    verified_peak_pct: float
    delta_pp: float
    length_above_8: float
    length_above_10: float
    length_above_12: float
    length_above_14: float
    fallback_used: bool


@dataclass
class FidelityReport:
    route_name: str
    backend: str
    coverage_pct: float
    climbs: list[ClimbVerification]
    missed_climbs: list[dict] = field(default_factory=list)
    verdict: str = "safe"


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def densify_polyline(
    coords: list[tuple[float, float]], stride_m: float
) -> list[tuple[float, float]]:
    """Insert intermediate points so consecutive samples are <= stride_m apart."""
    if len(coords) < 2:
        return list(coords)
    out: list[tuple[float, float]] = [coords[0]]
    for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
        d = haversine_m(la1, lo1, la2, lo2)
        n = max(1, int(math.ceil(d / stride_m)))
        for k in range(1, n):
            t = k / n
            out.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
        out.append((la2, lo2))
    return out


def smoothed_grades(
    elevs: list[float], dists: list[float], window_m: float = 30.0
) -> list[float]:
    """Per-point gradient % over a window_m centred span."""
    n = len(elevs)
    g = [0.0] * n
    for i in range(n):
        j = i
        while j > 0 and dists[i] - dists[j] < window_m:
            j -= 1
        k = i
        while k < n - 1 and dists[k] - dists[i] < window_m:
            k += 1
        dd = dists[k] - dists[j]
        if dd < 5.0:
            g[i] = 0.0
        else:
            g[i] = 100.0 * (elevs[k] - elevs[j]) / dd
    return g


def longest_run_above(
    grades: list[float], dists: list[float], threshold: float
) -> float:
    """Length (m) of the longest contiguous run with grade >= threshold."""
    best = 0.0
    start: Optional[int] = None
    for i, g in enumerate(grades):
        if g >= threshold:
            if start is None:
                start = i
            run = dists[i] - dists[start]
            if run > best:
                best = run
        else:
            start = None
    return best


def classify_verdict(deltas: list[float], missed: int) -> str:
    if missed > 0:
        return "high"
    worst = max(deltas) if deltas else 0.0
    if worst > 2.0:
        return "high"
    if worst > 1.0:
        return "minor"
    return "safe"
```

- [ ] **Step 3: Run tests, expect green**

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: 3 pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_climbs.py scripts/_tests/test_verify_climbs.py
git commit -m "Add verify_climbs core helpers (densify, gradients, verdict)"
```

---

## Task 5: `verify_climbs.py` — full route verification

**Files:**
- Modify: `scripts/verify_climbs.py`
- Modify: `scripts/_tests/test_verify_climbs.py`

Wire up `verify_route()` and `detect_missed_climbs()` against a `LocalDEM` + optional `GPXZClient`.

- [ ] **Step 1: Write failing tests using a fake DEM**

Append to `scripts/_tests/test_verify_climbs.py`:

```python
from verify_climbs import verify_route, detect_missed_climbs


class FakeDEM:
    """Returns elevation = 100 + 10 * sin-like ramp + a sharp 14% spike at km 7."""
    def __init__(self):
        self._covers = True
    def covers(self, lat, lon): return self._covers
    def sample(self, lat, lon):
        # latitude 51.0..51.001 → 0..111m horizontal
        # mimic the sample-route C2 spike
        offset_m = (lat - 51.0) * 111000
        if 6900 <= offset_m <= 7100:
            return 100 + 0.14 * (offset_m - 6900) + 0.05 * 6900
        return 100 + 0.05 * offset_m
    def sample_polyline(self, coords, stride_m):
        return [self.sample(la, lo) for la, lo in coords]


def test_verify_route_flags_spike():
    # synthetic GPX track from km 6.5 to km 7.5 along latitude
    coords = [(51.0 + i * 0.0001, 0.0) for i in range(60, 80)]
    climbs_from_gpx = [{
        "name": "C1",
        "km_start": 6.5,
        "km_end": 7.5,
        "coords": coords,
        "gpx_peak_pct": 9.0,
    }]
    dem = FakeDEM()
    report = verify_route(
        route_name="synthetic", climbs=climbs_from_gpx,
        full_route_coords=coords, dem=dem, fallback=None,
    )
    assert len(report.climbs) == 1
    cv = report.climbs[0]
    assert cv.verified_peak_pct > 12.0
    assert cv.delta_pp > 2.0
    assert report.verdict == "high"
```

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: new test fails (function not defined).

- [ ] **Step 2: Implement `verify_route` and `detect_missed_climbs`**

Append to `scripts/verify_climbs.py`:

```python
def _verify_one_climb(
    climb: dict, dem, fallback, stride_m: float = 5.0
) -> ClimbVerification:
    coords = climb["coords"]
    dense = densify_polyline(coords, stride_m=stride_m)

    fallback_used = False
    elevs: list[float] = []
    for lat, lon in dense:
        e = dem.sample(lat, lon)
        if e is None:
            if fallback is not None and fallback.configured:
                # Fall back per-segment: collect Nones and call API once.
                e = None
            else:
                e = float("nan")
        elevs.append(e if e is not None else float("nan"))

    # If any NaN and fallback is configured, fill them in one batched call.
    nan_idx = [i for i, e in enumerate(elevs) if math.isnan(e)]
    if nan_idx and fallback is not None and fallback.configured:
        miss_coords = [dense[i] for i in nan_idx]
        filled = fallback.sample_polyline(miss_coords)
        for i, v in zip(nan_idx, filled):
            elevs[i] = v
        fallback_used = True

    # Drop NaNs that remain (no fallback / fallback failed)
    clean = [(p, e) for p, e in zip(dense, elevs) if not math.isnan(e)]
    if len(clean) < 2:
        return ClimbVerification(
            name=climb["name"], km_start=climb["km_start"],
            km_end=climb["km_end"], gpx_peak_pct=climb["gpx_peak_pct"],
            verified_peak_pct=float("nan"), delta_pp=float("nan"),
            length_above_8=0.0, length_above_10=0.0,
            length_above_12=0.0, length_above_14=0.0,
            fallback_used=fallback_used,
        )
    coords_clean = [c for c, _ in clean]
    elevs_clean = [e for _, e in clean]
    dists = [0.0]
    for (la1, lo1), (la2, lo2) in zip(coords_clean, coords_clean[1:]):
        dists.append(dists[-1] + haversine_m(la1, lo1, la2, lo2))
    grades = smoothed_grades(elevs_clean, dists, window_m=30.0)

    peak = max(grades) if grades else 0.0
    return ClimbVerification(
        name=climb["name"], km_start=climb["km_start"],
        km_end=climb["km_end"], gpx_peak_pct=climb["gpx_peak_pct"],
        verified_peak_pct=peak,
        delta_pp=peak - climb["gpx_peak_pct"],
        length_above_8=longest_run_above(grades, dists, 8.0),
        length_above_10=longest_run_above(grades, dists, 10.0),
        length_above_12=longest_run_above(grades, dists, 12.0),
        length_above_14=longest_run_above(grades, dists, 14.0),
        fallback_used=fallback_used,
    )


def detect_missed_climbs(
    full_coords: list[tuple[float, float]],
    dem,
    fallback,
    known_ranges_km: list[tuple[float, float]],
    min_length_m: float = 300.0,
    min_gain_m: float = 20.0,
    stride_m: float = 25.0,
) -> list[dict]:
    """Walk the full route and flag climbs not already covered by known_ranges."""
    dense = densify_polyline(full_coords, stride_m=stride_m)
    dists = [0.0]
    for (la1, lo1), (la2, lo2) in zip(dense, dense[1:]):
        dists.append(dists[-1] + haversine_m(la1, lo1, la2, lo2))
    elevs = [dem.sample(la, lo) for la, lo in dense]
    nan_idx = [i for i, e in enumerate(elevs) if e is None]
    if nan_idx and fallback is not None and fallback.configured:
        miss_coords = [dense[i] for i in nan_idx]
        filled = fallback.sample_polyline(miss_coords)
        for i, v in zip(nan_idx, filled):
            elevs[i] = v
    if any(e is None for e in elevs):
        # Drop None segments rather than fail
        valid = [(d, e) for d, e in zip(dists, elevs) if e is not None]
        if len(valid) < 2:
            return []
        dists = [d for d, _ in valid]
        elevs = [e for _, e in valid]

    grades = smoothed_grades(elevs, dists, window_m=30.0)

    # Walk to find rising segments
    found: list[dict] = []
    i = 0
    n = len(grades)
    while i < n:
        if grades[i] >= 3.0:
            j = i
            while j < n and grades[j] >= 3.0:
                j += 1
            length = dists[j - 1] - dists[i]
            gain = elevs[j - 1] - elevs[i]
            avg = 100 * gain / length if length > 0 else 0.0
            peak = max(grades[i:j]) if j > i else 0.0
            km_mid = (dists[i] + dists[j - 1]) / 2000
            if length >= min_length_m and gain >= min_gain_m:
                already_known = any(
                    ks <= km_mid <= ke for ks, ke in known_ranges_km
                )
                if not already_known:
                    found.append({
                        "km": dists[i] / 1000,
                        "length_m": length,
                        "gain_m": gain,
                        "avg_pct": avg,
                        "peak_pct": peak,
                    })
            i = j
        else:
            i += 1
    return found


def verify_route(
    route_name: str,
    climbs: list[dict],
    full_route_coords: list[tuple[float, float]],
    dem,
    fallback,
) -> FidelityReport:
    verifications = [_verify_one_climb(c, dem, fallback) for c in climbs]
    known_ranges = [(c["km_start"], c["km_end"]) for c in climbs]
    missed = detect_missed_climbs(full_route_coords, dem, fallback, known_ranges)

    any_fallback = any(v.fallback_used for v in verifications)
    backend = "mixed" if any_fallback else "local-1m"
    deltas = [v.delta_pp for v in verifications if not math.isnan(v.delta_pp)]
    verdict = classify_verdict(deltas, missed=len(missed))

    # crude coverage stat: % of original climb coords inside the DEM
    total = sum(len(c["coords"]) for c in climbs)
    covered = sum(
        1 for c in climbs for la, lo in c["coords"] if dem.covers(la, lo)
    )
    coverage_pct = 100.0 * covered / total if total else 100.0

    return FidelityReport(
        route_name=route_name, backend=backend,
        coverage_pct=coverage_pct, climbs=verifications,
        missed_climbs=missed, verdict=verdict,
    )
```

- [ ] **Step 3: Run tests, expect green**

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_climbs.py scripts/_tests/test_verify_climbs.py
git commit -m "Add verify_route and detect_missed_climbs orchestration"
```

---

## Task 6: Fidelity Report rendering + analyse_gpx integration

**Files:**
- Modify: `scripts/verify_climbs.py` (add `render_report` + `embed_in_prediction`)
- Modify: `scripts/analyse_gpx.py` (default-on `--verify`, `--no-verify`)
- Modify: `scripts/_tests/test_verify_climbs.py`

- [ ] **Step 1: Write failing test for renderer**

Append to `scripts/_tests/test_verify_climbs.py`:

```python
from verify_climbs import render_report, embed_in_prediction, FidelityReport, ClimbVerification


def test_render_report_includes_verdict_and_table():
    cv = ClimbVerification(
        name="C1", km_start=6.75, km_end=8.70,
        gpx_peak_pct=9.3, verified_peak_pct=14.3, delta_pp=5.0,
        length_above_8=248, length_above_10=192, length_above_12=116,
        length_above_14=4, fallback_used=False,
    )
    report = FidelityReport(
        route_name="test", backend="local-1m", coverage_pct=100.0,
        climbs=[cv], missed_climbs=[], verdict="high",
    )
    text = render_report(report)
    assert "HIGH RISK" in text or "high" in text.lower()
    assert "14.3" in text
    assert "+5.0" in text or "5.0pp" in text
    assert "<!-- BEGIN FIDELITY -->" in text
    assert "<!-- END FIDELITY -->" in text


def test_embed_in_prediction_idempotent(tmp_path):
    md = tmp_path / "x-prediction.md"
    md.write_text("# Route\n\n## TSS estimate\nfoo\n")
    cv = ClimbVerification(
        name="C1", km_start=0, km_end=1, gpx_peak_pct=5, verified_peak_pct=5,
        delta_pp=0, length_above_8=0, length_above_10=0, length_above_12=0,
        length_above_14=0, fallback_used=False,
    )
    report = FidelityReport(
        route_name="x", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    embed_in_prediction(md, report)
    embed_in_prediction(md, report)  # second call should not duplicate
    assert md.read_text().count("<!-- BEGIN FIDELITY -->") == 1
```

- [ ] **Step 2: Implement renderer + embed**

Append to `scripts/verify_climbs.py`:

```python
import re

VERDICT_LINE = {
    "safe": "Safe to plan — gradients within ±1pp.",
    "minor": "Minor risk — peak gradient understated by up to 2pp.",
    "high": "HIGH RISK — peak gradients underestimated and/or climbs missing.",
}


def render_report(report: FidelityReport) -> str:
    lines = ["<!-- BEGIN FIDELITY -->", "## Fidelity Report", ""]
    lines.append(f"**Verdict:** {VERDICT_LINE[report.verdict]}")
    lines.append(f"**Backend:** {report.backend}  ")
    lines.append(f"**Coverage:** {report.coverage_pct:.0f}%  ")
    if any(c.fallback_used for c in report.climbs):
        lines.append("*(Some climbs verified via GPXZ API fallback.)*  ")
    lines.append("")
    lines.append("### Per-climb comparison")
    lines.append("")
    lines.append("| # | km | GPX peak | Verified peak | Δ | >12% | >10% | >8% |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(report.climbs, start=1):
        if math.isnan(c.verified_peak_pct):
            lines.append(
                f"| {i} | {c.km_start:.2f} | {c.gpx_peak_pct:.1f}% | "
                "(unverified) | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {i} | {c.km_start:.2f} | {c.gpx_peak_pct:.1f}% | "
            f"**{c.verified_peak_pct:.1f}%** | "
            f"{'+' if c.delta_pp >= 0 else ''}{c.delta_pp:.1f}pp | "
            f"{c.length_above_12:.0f}m | "
            f"{c.length_above_10:.0f}m | "
            f"{c.length_above_8:.0f}m |"
        )
    if report.missed_climbs:
        lines.append("")
        lines.append("### Missed climbs (in DEM, not in GPX)")
        lines.append("")
        lines.append("| km | Length | Gain | Avg % | Peak % |")
        lines.append("|---|---|---|---|---|")
        for m in report.missed_climbs:
            lines.append(
                f"| {m['km']:.2f} | {m['length_m']:.0f}m | "
                f"{m['gain_m']:.0f}m | {m['avg_pct']:.1f}% | "
                f"{m['peak_pct']:.1f}% |"
            )
    lines.append("")
    lines.append("<!-- END FIDELITY -->")
    return "\n".join(lines) + "\n"


_FIDELITY_BLOCK = re.compile(
    r"<!-- BEGIN FIDELITY -->.*?<!-- END FIDELITY -->\n?",
    re.DOTALL,
)


def embed_in_prediction(md_path: Path, report: FidelityReport) -> None:
    block = render_report(report)
    text = md_path.read_text() if md_path.exists() else ""
    if _FIDELITY_BLOCK.search(text):
        text = _FIDELITY_BLOCK.sub(block, text)
    else:
        # Insert after first H1 heading and any blank line
        m = re.search(r"^# .+\n", text, re.MULTILINE)
        if m:
            insert_at = m.end()
            text = text[:insert_at] + "\n" + block + "\n" + text[insert_at:]
        else:
            text = block + "\n" + text
    md_path.write_text(text)
```

- [ ] **Step 3: Run tests, expect green**

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: all tests pass.

- [ ] **Step 4: Wire into `analyse_gpx.py`**

Modify `scripts/analyse_gpx.py`:

1. Add CLI flags. Find the existing `argparse` setup and add:

```python
parser.add_argument(
    "--no-verify",
    action="store_true",
    help="Skip Fidelity Report generation against on-device DEM.",
)
parser.add_argument(
    "--coverage-gap",
    choices=["download", "api", "skip", "fail"],
    default=None,
    help="Policy when route extends outside loaded DEM tiles. "
         "Defaults: 'download' (interactive), 'api' (non-interactive with key), "
         "'skip' (non-interactive without key).",
)
parser.add_argument(
    "--dem-root",
    default=str(Path.home() / "cycling-coach-dem"),
    help="Path to local DEM tile root.",
)
```

2. After the existing prediction MD is written (look for the final write of `routes/<name>-prediction.md` content), add:

```python
if not args.no_verify and args.save:
    try:
        from local_dem import LocalDEM
        from elevation_fallback import GPXZClient
        from verify_climbs import verify_route, embed_in_prediction

        dem_root = Path(args.dem_root)
        dem = LocalDEM(dem_root) if dem_root.exists() else None
        fallback = GPXZClient()

        # Build climb dicts the verifier expects
        verifier_climbs = [
            {
                "name": f"C{i+1}",
                "km_start": c["km_start"],
                "km_end": c["km_end"],
                "gpx_peak_pct": c["max_grade_pct"],
                "coords": [
                    (pt["lat"], pt["lon"])
                    for pt in trkpts
                    if c["km_start"] * 1000 <= pt["cum_m"] <= c["km_end"] * 1000
                ],
            }
            for i, c in enumerate(climbs)
        ]
        full_coords = [(pt["lat"], pt["lon"]) for pt in trkpts]

        if dem is None:
            print("⚠ No DEM tiles found at", dem_root, "— skipping verification.", file=sys.stderr)
        else:
            report = verify_route(
                route_name=track_name,
                climbs=verifier_climbs,
                full_route_coords=full_coords,
                dem=dem, fallback=fallback,
            )
            embed_in_prediction(out_md, report)
            print(f"  Embedded Fidelity Report ({report.verdict}) in {out_md}")
    except Exception as e:
        print(f"⚠ Verification failed: {e}", file=sys.stderr)
```

(The exact variable names — `trkpts`, `climbs`, `out_md`, `track_name`, `args.save` — must match what `analyse_gpx.py` actually uses. Read the file before editing and adapt names.)

- [ ] **Step 5: Smoke-test against the sample route GPX**

Run:
```bash
/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py \
    routes/2026-05-09-sample-route.gpx \
    --save --no-verify
```
Expected: pre-existing behaviour unchanged, prediction MD regenerated.

Run again without `--no-verify` (DEM tiles likely not yet downloaded):
```bash
/opt/miniconda3/envs/cycling/bin/python scripts/analyse_gpx.py \
    routes/2026-05-09-sample-route.gpx --save
```
Expected: warning "⚠ No DEM tiles found" printed, prediction MD still written without fidelity block.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_climbs.py scripts/analyse_gpx.py scripts/_tests/test_verify_climbs.py
git commit -m "Render Fidelity Report and embed into analyse_gpx output"
```

---

## Task 7: `fetch_dem_tiles.py` — bulk tile downloader

**Files:**
- Create: `scripts/fetch_dem_tiles.py`
- Create: `scripts/_tests/test_fetch_dem_tiles.py`

**Public API:**
```python
def bbox_from_gpx(path: Path) -> tuple[float, float, float, float]: ...
def os_grid_tiles_for_bbox(bbox) -> list[str]: ...   # e.g. ["TQ45", "TQ55"]
def ign_tiles_for_bbox(bbox) -> list[str]: ...
def fetch_tiles(tile_ids: list[str], region: str, dest_root: Path) -> dict: ...
def main(argv): ...
```

The DEFRA "OS Grid" tiles cover the UK in 10km × 10km squares (e.g. TQ45). IGN tiles cover France similarly. The fetch URL format depends on the live endpoints — those will be researched at implementation time and configured via constants at the top of the module. For the plan we treat them as pluggable.

- [ ] **Step 1: Write failing tests for grid math + idempotent fetch**

Write `scripts/_tests/test_fetch_dem_tiles.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch_dem_tiles import (
    bbox_from_gpx, os_grid_tiles_for_bbox, fetch_tiles,
)


def test_bbox_from_gpx(tmp_path):
    gpx = tmp_path / "t.gpx"
    gpx.write_text("""<?xml version='1.0'?>
<gpx version='1.1' xmlns='http://www.topografix.com/GPX/1/1'>
<trk><trkseg>
<trkpt lat='51.0' lon='0.0'/><trkpt lat='51.1' lon='0.2'/>
</trkseg></trk></gpx>""")
    bbox = bbox_from_gpx(gpx)
    assert bbox == (0.0, 51.0, 0.2, 51.1)


def test_os_grid_tiles_includes_TQ_for_kent():
    # bbox covering part of Kent
    tiles = os_grid_tiles_for_bbox((0.0, 51.1, 0.5, 51.3))
    assert any(t.startswith("TQ") for t in tiles)


def test_fetch_tiles_skips_already_present(tmp_path):
    (tmp_path / "uk-1m" / "TQ").mkdir(parents=True)
    (tmp_path / "uk-1m" / "TQ" / "TQ45.tif").write_bytes(b"x" * 1024)
    with patch("fetch_dem_tiles._download_one") as dl:
        result = fetch_tiles(["TQ45"], region="uk", dest_root=tmp_path)
    dl.assert_not_called()
    assert result["skipped"] == ["TQ45"]


def test_fetch_tiles_downloads_missing(tmp_path):
    with patch("fetch_dem_tiles._download_one") as dl:
        dl.return_value = True
        result = fetch_tiles(["TQ45"], region="uk", dest_root=tmp_path)
    dl.assert_called_once()
    assert result["downloaded"] == ["TQ45"]
```

- [ ] **Step 2: Implement `fetch_dem_tiles.py`**

```python
"""Bulk DEM tile downloader for UK (DEFRA OGL v3) and France (IGN Etalab 2.0).

Idempotent: skips tiles already present, deletes partial downloads on failure.
Endpoints are configurable via the URL_TEMPLATES dict at the top of the module
since the public DEFRA / IGN endpoints occasionally restructure.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import requests
from pyproj import Transformer

# Endpoint templates — update at integration time per current DEFRA / IGN docs.
URL_TEMPLATES = {
    "uk": "https://environment.data.gov.uk/api/lidar/tile/{tile}.tif",
    "fr": "https://geoservices.ign.fr/rgealti/{tile}.tif",
}

PRESETS = {
    "surrey-kent": ("uk", (-0.6, 51.05, 0.6, 51.5)),
    "greater-london": ("uk", (-0.55, 51.30, 0.30, 51.70)),
    "ile-de-france": ("fr", (1.45, 48.10, 3.55, 49.25)),
}


def bbox_from_gpx(path: Path) -> tuple[float, float, float, float]:
    tree = ET.parse(str(path))
    root = tree.getroot()
    ns = {"g": root.tag.split("}")[0].strip("{")}
    pts = root.findall(".//g:trkpt", ns)
    lats = [float(p.attrib["lat"]) for p in pts]
    lons = [float(p.attrib["lon"]) for p in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def os_grid_tiles_for_bbox(bbox) -> list[str]:
    """Return the 10km OS grid squares that cover the bbox (e.g. ['TQ45'])."""
    minlon, minlat, maxlon, maxlat = bbox
    tr = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    e_min, n_min = tr.transform(minlon, minlat)
    e_max, n_max = tr.transform(maxlon, maxlat)
    tiles: set[str] = set()
    for e in range(int(e_min) // 10000, int(e_max) // 10000 + 1):
        for n in range(int(n_min) // 10000, int(n_max) // 10000 + 1):
            tiles.add(_os_grid_label(e * 10000, n * 10000))
    return sorted(t for t in tiles if t)


def _os_grid_label(easting: int, northing: int) -> str:
    """OS Grid 100km letter pair + 10km digits."""
    if easting < 0 or northing < 0 or easting >= 700000 or northing >= 1300000:
        return ""
    e100, n100 = easting // 100000, northing // 100000
    # 5x5 grid of 500km squares
    e500, n500 = e100 // 5, n100 // 5
    e_in, n_in = e100 % 5, n100 % 5
    first_idx = (4 - n500) * 5 + e500  # row from top
    second_idx = (4 - n_in) * 5 + e_in
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # I omitted
    first = letters[first_idx]
    second = letters[second_idx]
    e10 = (easting % 100000) // 10000
    n10 = (northing % 100000) // 10000
    return f"{first}{second}{e10}{n10}"


def ign_tiles_for_bbox(bbox) -> list[str]:
    """Return IGN RGE ALTI tile IDs covering the bbox (1km tiles, names like
    'IGN_LAMB93_E0625_N6875')."""
    minlon, minlat, maxlon, maxlat = bbox
    tr = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    e_min, n_min = tr.transform(minlon, minlat)
    e_max, n_max = tr.transform(maxlon, maxlat)
    tiles: list[str] = []
    for e in range(int(e_min) // 1000, int(e_max) // 1000 + 1):
        for n in range(int(n_min) // 1000, int(n_max) // 1000 + 1):
            tiles.append(f"IGN_LAMB93_E{e:04d}_N{n:04d}")
    return tiles


def _download_one(url: str, dest: Path) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
        tmp.rename(dest)
        return True
    except Exception:
        if tmp.exists():
            tmp.unlink()
        return False


def fetch_tiles(
    tile_ids: Iterable[str], region: str, dest_root: Path
) -> dict:
    sub = "uk-1m" if region == "uk" else "fr-1m"
    base = Path(dest_root) / sub
    skipped: list[str] = []
    downloaded: list[str] = []
    failed: list[str] = []
    for t in tile_ids:
        sub_dir = t[:2] if region == "uk" else "ile-de-france"
        out = base / sub_dir / f"{t}.tif"
        if out.exists() and out.stat().st_size > 0:
            skipped.append(t)
            continue
        url = URL_TEMPLATES[region].format(tile=t)
        ok = _download_one(url, out)
        (downloaded if ok else failed).append(t)
    _update_coverage(dest_root, region, downloaded)
    return {"skipped": skipped, "downloaded": downloaded, "failed": failed}


def _update_coverage(dest_root: Path, region: str, new_tiles: list[str]):
    cov = Path(dest_root) / "coverage.json"
    data = json.loads(cov.read_text()) if cov.exists() else {}
    data.setdefault(region, [])
    for t in new_tiles:
        if t not in data[region]:
            data[region].append(t)
    dest_root.mkdir(parents=True, exist_ok=True)
    cov.write_text(json.dumps(data, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat")
    g.add_argument("--gpx", type=Path)
    g.add_argument("--region", choices=list(PRESETS))
    p.add_argument("--country", choices=["uk", "fr"], default="uk")
    p.add_argument("--dest", default=str(Path.home() / "cycling-coach-dem"))
    args = p.parse_args(argv)

    if args.region:
        country, bbox = PRESETS[args.region]
    else:
        country = args.country
        if args.bbox:
            bbox = tuple(float(x) for x in args.bbox.split(","))
        else:
            bbox = bbox_from_gpx(args.gpx)

    tiles = os_grid_tiles_for_bbox(bbox) if country == "uk" else ign_tiles_for_bbox(bbox)
    print(f"Region {country}, bbox {bbox}, tiles: {len(tiles)}")
    result = fetch_tiles(tiles, region=country, dest_root=Path(args.dest))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests, expect green**

Run: `pytest scripts/_tests/test_fetch_dem_tiles.py -v`
Expected: all 4 tests pass.

- [ ] **Step 4: Verify the OS-grid math against a known reference**

Run a one-off:
```bash
/opt/miniconda3/envs/cycling/bin/python -c \
"from sys import path; path.insert(0,'scripts'); \
from fetch_dem_tiles import os_grid_tiles_for_bbox; \
print(os_grid_tiles_for_bbox((-0.13,51.50,-0.10,51.52)))"
```
Expected: at least one TQ-prefixed tile (central London is TQ28/TQ38).

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_dem_tiles.py scripts/_tests/test_fetch_dem_tiles.py
git commit -m "Add fetch_dem_tiles bulk downloader (UK OS-grid + IGN Lambert93)"
```

---

## Task 8: Coverage-gap interactive prompt

**Files:**
- Modify: `scripts/verify_climbs.py` (add `prompt_coverage_gap`)
- Modify: `scripts/analyse_gpx.py` (call prompt before verify when DEM partial)
- Modify: `scripts/_tests/test_verify_climbs.py`

- [ ] **Step 1: Write failing test for prompt logic**

Append to `scripts/_tests/test_verify_climbs.py`:

```python
from verify_climbs import resolve_coverage_policy


def test_resolve_policy_explicit_flag():
    assert resolve_coverage_policy(flag="api", interactive=True, has_key=True) == "api"


def test_resolve_policy_default_interactive():
    assert resolve_coverage_policy(flag=None, interactive=True, has_key=False) == "prompt"


def test_resolve_policy_default_non_interactive_with_key():
    assert resolve_coverage_policy(flag=None, interactive=False, has_key=True) == "api"


def test_resolve_policy_default_non_interactive_no_key():
    assert resolve_coverage_policy(flag=None, interactive=False, has_key=False) == "skip"
```

- [ ] **Step 2: Implement `resolve_coverage_policy`**

Append to `scripts/verify_climbs.py`:

```python
def resolve_coverage_policy(flag: str | None, interactive: bool, has_key: bool) -> str:
    if flag is not None:
        return flag
    if interactive:
        return "prompt"
    return "api" if has_key else "skip"


def prompt_coverage_gap(missing_tiles: list[str], total_mb: int) -> str:
    """Interactive prompt; returns 'download' / 'api' / 'skip' / 'quit'."""
    print(f"Route extends outside loaded DEM tiles.")
    print(f"Missing tiles: {', '.join(missing_tiles[:10])}"
          + (f"... ({len(missing_tiles)} total)" if len(missing_tiles) > 10 else ""))
    print(f"Estimated download size: ~{total_mb} MB")
    print()
    print("  [d] Download missing tiles now and verify locally   (recommended)")
    print("  [a] Use GPXZ API for the uncovered segments only")
    print("  [s] Skip verification on uncovered segments and proceed")
    print("  [q] Quit")
    while True:
        choice = input("Your choice [d]: ").strip().lower() or "d"
        if choice in ("d", "download"): return "download"
        if choice in ("a", "api"): return "api"
        if choice in ("s", "skip"): return "skip"
        if choice in ("q", "quit"): return "quit"
        print("Please answer d / a / s / q.")
```

- [ ] **Step 3: Run tests**

Run: `pytest scripts/_tests/test_verify_climbs.py -v`
Expected: all pass (the 4 new policy tests + existing).

- [ ] **Step 4: Wire prompt into `analyse_gpx.py`**

In the verification block added in Task 6, before calling `verify_route`, identify missing tiles up-front:

```python
import sys as _sys
from verify_climbs import resolve_coverage_policy, prompt_coverage_gap

# Build full list of route coords
full_coords = [(pt["lat"], pt["lon"]) for pt in trkpts]
uncovered = [c for c in full_coords if not dem.covers(*c)]
if uncovered:
    interactive = _sys.stdin.isatty()
    has_key = fallback.configured
    policy = resolve_coverage_policy(args.coverage_gap, interactive, has_key)
    if policy == "prompt":
        # Compute missing tile IDs from uncovered coords
        from fetch_dem_tiles import (
            os_grid_tiles_for_bbox, ign_tiles_for_bbox, fetch_tiles,
        )
        lats = [c[0] for c in uncovered]; lons = [c[1] for c in uncovered]
        bbox = (min(lons), min(lats), max(lons), max(lats))
        is_uk = -8 < bbox[0] < 2 and 49 < bbox[1] < 61
        missing = (
            os_grid_tiles_for_bbox(bbox) if is_uk else ign_tiles_for_bbox(bbox)
        )
        policy = prompt_coverage_gap(missing, total_mb=len(missing) * 50)
    if policy == "download":
        fetch_tiles(missing, region="uk" if is_uk else "fr", dest_root=Path(args.dem_root))
        dem = LocalDEM(Path(args.dem_root))  # reload
    elif policy == "skip":
        fallback = None  # disable fallback so verify just leaves NaN where missing
    elif policy == "quit":
        print("Aborted by user.", file=_sys.stderr)
        return
    # "api" — keep fallback enabled, no action needed
```

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_climbs.py scripts/analyse_gpx.py scripts/_tests/test_verify_climbs.py
git commit -m "Add coverage-gap policy resolution and interactive prompt"
```

---

## Task 9: End-to-end regression test on sample-route

**Files:**
- Create: `scripts/_tests/test_sample-route_regression.py`

**Goal:** Smoke-test the whole pipeline against the real sample-route route, confirming the verifier flags C2/C3/C8 underestimates and detects the missed Climb 7. This task is OPTIONAL until DEM tiles are downloaded; mark it `pytest.mark.skipif` when tiles are absent.

- [ ] **Step 1: Write the regression test**

```python
import sys
import os
import pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEM_ROOT = Path.home() / "cycling-coach-dem"
GPX = Path("routes/2026-05-09-sample-route.gpx")


@pytest.mark.skipif(
    not DEM_ROOT.exists() or not GPX.exists(),
    reason="Requires downloaded DEM tiles and sample-route route GPX",
)
def test_sample-route_v2_flags_known_underestimates():
    from local_dem import LocalDEM
    from elevation_fallback import GPXZClient
    from verify_climbs import verify_route
    # Re-derive baseline climbs from analyse_gpx (call it programmatically or
    # parse its JSON output). Simplest: invoke as subprocess with --json.
    import subprocess, json
    out = subprocess.check_output([
        sys.executable, "scripts/analyse_gpx.py", str(GPX), "--json", "--no-verify"
    ])
    base = json.loads(out)

    dem = LocalDEM(DEM_ROOT)
    fallback = GPXZClient()
    verifier_climbs = [
        {
            "name": f"C{i+1}",
            "km_start": c["km_start"],
            "km_end": c["km_end"],
            "gpx_peak_pct": c["max_grade_pct"],
            "coords": [(p["lat"], p["lon"]) for p in c["coords"]],
        }
        for i, c in enumerate(base["climbs"])
    ]
    full = [(p["lat"], p["lon"]) for p in base["trackpoints"]]
    report = verify_route(
        route_name="sample-route-v2",
        climbs=verifier_climbs, full_route_coords=full,
        dem=dem, fallback=fallback,
    )

    # C2/C3/C8 (km 6.75 / 12.85 / 47.95) should each show >3pp underestimate
    big_deltas = [c for c in report.climbs if c.delta_pp > 3.0]
    assert len(big_deltas) >= 3
    # Climb 7 (km 46.55) should appear as a missed climb
    assert any(46.0 < m["km"] < 47.0 for m in report.missed_climbs)
    assert report.verdict == "high"
```

(If `analyse_gpx.py --json` doesn't currently emit `coords` per climb or `trackpoints`, extend the JSON output minimally as part of this task — the alternative is duplicating parse logic in the test.)

- [ ] **Step 2: Run when tiles are available**

Run: `pytest scripts/_tests/test_sample-route_regression.py -v`
Expected: pass once `~/cycling-coach-dem/uk-1m/TQ/` is populated with the relevant Surrey/Kent tiles via `fetch_dem_tiles.py --region surrey-kent`.

- [ ] **Step 3: Commit**

```bash
git add scripts/_tests/test_sample-route_regression.py
git commit -m "Add sample-route end-to-end regression test (skipped without tiles)"
```

---

## Task 10: Documentation update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a "Climb verification" workflow note**

Find the "Workflow expectations" section and append:

```markdown
When the rider provides a GPX file and the verifier is enabled (default):
1. After `analyse_gpx.py` runs, `verify_climbs` re-samples each climb against `~/cycling-coach-dem/`
2. If tiles are missing, the rider is prompted to download / use API / skip
3. The Fidelity Report is embedded inline in `routes/<name>-prediction.md` between `<!-- BEGIN FIDELITY -->` markers
4. To download tiles for a new region: `python scripts/fetch_dem_tiles.py --region <preset>` or `--gpx <route>`
5. To skip verification (offline use): `python scripts/analyse_gpx.py <gpx> --save --no-verify`

DEM tiles live at `~/cycling-coach-dem/{uk-1m,fr-1m}/`. The GPXZ API key (free non-commercial tier) lives at `~/.config/cycling-coach/gpxz.key`. Both are outside the repo.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Document climb verifier workflow in CLAUDE.md"
```

---

## Self-review checklist (already applied)

- [x] Spec coverage: every component, failure mode, decision in the spec maps to a task above.
- [x] No placeholders: every code step contains the actual code; no "TBD".
- [x] Type consistency: `ClimbVerification` and `FidelityReport` field names match across tasks.
- [x] Conda env portability rule covered in Task 1 + already in CLAUDE.md.
- [x] Tests follow TDD: failing test → minimal implementation → passing test → commit, every task.

---

## Known integration points to confirm at execution time

These details depend on the current internal shape of `analyse_gpx.py` and the DEFRA/IGN endpoints, both of which the implementer should re-read before coding:

1. **`analyse_gpx.py` JSON output**: ensure `--json` includes per-climb `coords` and full `trackpoints`. If not, extend it as part of Task 6 or Task 9.
2. **DEFRA LIDAR composite endpoint**: the URL template in `fetch_dem_tiles.py:URL_TEMPLATES["uk"]` is illustrative. Replace with the current public endpoint (likely a WCS service or signed-URL pattern) at implementation time.
3. **IGN RGE ALTI endpoint**: same caveat — IGN serves through its Géoplateforme; resolve the actual download URL pattern before running.
