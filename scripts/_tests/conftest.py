import sys
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from pathlib import Path

# Put scripts/ on sys.path (mirrors pyproject's pythonpath; also covers
# direct `pytest path/to/test.py` invocations from odd cwds).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --- Synthetic rider profile -------------------------------------------------
# A full nested profile (two bikes, one assisted, one peer) written to a temp
# file. Tests load via this fixture so they NEVER read the rider's real,
# gitignored USER_PROFILE.md — closing the long-standing test-fragility where
# importing profile-touching modules depended on live data.

SYNTHETIC_PROFILE_TEXT = """\
---
identity:
  name: Test Rider
  location: Nowhere

body:
  weight_kg: 80.0

fitness:
  ftp_w: 250
  map_w_working: 300
  map_w_test: 295
  ac_w: 360
  nm_w: 600
  lthr_bpm: 168
  max_hr_bpm: 190
  rest_hr_bpm: 50
  note: |
    A block scalar containing a colon: this line must NOT leak as a key.
    ftp_w: 999

default_bike: roadie

bikes:
  roadie:
    name: Test Roadie
    bike_weight_kg: 8.5
    system_weight_kg_default: 90.0
    fr_split: "45/55"
    cda: 0.30
    cda_range: "0.28-0.32"
    drivetrain_efficiency: 0.97
    wheel_circ_m: 2.105
    has_power_meter: true
    tyres:
      model: Test GP5000
      size_mm: 28
    crr_by_surface:
      tarmac: 0.0050
    surfaces_supported: [tarmac]
    gearing:
      chainrings_t: [34, 50]
      cassette_t: [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32]

  ebike:
    name: Test E-Bike
    bike_weight_kg: 20.0
    system_weight_kg_default: 102.0
    fr_split: "40/60"
    cda: 0.42
    drivetrain_efficiency: 0.96
    wheel_circ_m: 1.59
    has_power_meter: false
    tyres: {}
    crr_by_surface:
      tarmac: 0.0100
      gravel_smooth: 0.0180
    surfaces_supported: [tarmac, gravel]
    assist:
      type: e-Motiq
      placement: rear_hub
      rated_w: 250
      peak_w: null
      torque_nm: null
      sensor: torque
      legal_cutoff_kph: 25
      levels:
        L0: {name: none, share: 0.0}
        L1: {name: low, share: 0.5}
        L2: {name: high, share: 1.0}
      battery_wh: 345
      battery_range_km: {manual_typical: "30-70"}
      default_level_flat: L1
      default_level_climb_5pct: L2
      default_level_climb_10pct: L2

peer_alex:
  label: Alex
  ftp_w: 240
  weight_kg: 72
---

# Test rider context
Body text that must be ignored by the frontmatter parser.
"""


@pytest.fixture
def synthetic_profile_path(tmp_path):
    """Path to a temp USER_PROFILE.md-shaped file (full nested schema)."""
    p = tmp_path / "USER_PROFILE.md"
    p.write_text(SYNTHETIC_PROFILE_TEXT, encoding="utf-8")
    return p


@pytest.fixture
def synthetic_profile(synthetic_profile_path):
    """The loaded+merged synthetic profile dict (defaults + active bike)."""
    import profile
    return profile.load_profile(synthetic_profile_path)


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
