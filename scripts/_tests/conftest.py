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
