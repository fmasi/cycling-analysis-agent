"""Bilinear-sampling correctness for LocalDEM.

A non-uniform DEM (every pixel distinct) catches the half-pixel offset that a
constant-slope ramp cannot: sampling exactly at a pixel centre must return that
pixel's value, and sampling the midpoint between two centres must return their
mean.
"""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from local_dem import LocalDEM


def _make_dem(tmp_path):
    # 6x6, value = row*10 + col → every pixel distinct, non-linear across both axes.
    arr = np.fromfunction(lambda r, c: r * 10 + c, (6, 6), dtype=np.float32)
    transform = from_origin(0.0, 51.0006, 0.0001, 0.0001)  # ~11 m px near 51°N
    path = tmp_path / "nonuniform.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=6, width=6, count=1,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(arr, 1)
    return path, transform, arr


def test_sample_at_pixel_centre_returns_pixel_value(tmp_path):
    path, transform, arr = _make_dem(tmp_path)
    dem = LocalDEM(tmp_path)
    with rasterio.open(path) as ds:
        for r, c in [(2, 2), (3, 1), (1, 4)]:
            x, y = ds.xy(r, c)           # world coord of pixel (r,c) CENTRE
            val = dem.sample(lat=y, lon=x)
            assert val is not None
            assert abs(val - arr[r, c]) < 1e-4, (r, c, val, arr[r, c])


def test_sample_between_two_centres_is_their_mean(tmp_path):
    path, transform, arr = _make_dem(tmp_path)
    dem = LocalDEM(tmp_path)
    with rasterio.open(path) as ds:
        x0, y0 = ds.xy(2, 2)
        x1, y1 = ds.xy(2, 3)             # horizontal neighbour
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        val = dem.sample(lat=my, lon=mx)
        assert abs(val - (arr[2, 2] + arr[2, 3]) / 2) < 1e-4
