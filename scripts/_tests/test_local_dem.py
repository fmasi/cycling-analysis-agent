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
    out = dem.sample_polyline(coords)
    assert out[0] is not None
    assert out[-1] is None


def test_ramp_gradient_is_correct(synthetic_dem_dir):
    """Synthetic ramp: south→north, 100m→200m over ~111m. Grade ≈ 90%.
    We test the relative direction, not the exact value (CRS rounding)."""
    dem = LocalDEM(synthetic_dem_dir)
    south = dem.sample(51.0001, 0.0005)
    north = dem.sample(51.0009, 0.0005)
    assert north > south
