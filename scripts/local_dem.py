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
        # Use the affine transform inverse to get fractional row/col reliably
        # across rasterio versions (some don't accept op=float on .index()).
        col_f, row_f = ~ds.transform * (x, y)
        # The affine maps integer (col,row) to a pixel's upper-left CORNER, so
        # pixel (r,c)'s centre sits at (c+0.5, r+0.5). Shift by -0.5 so the
        # bilinear weights interpolate between pixel CENTRES; without this every
        # sample is offset half a pixel (biases peak-gradient on steep walls).
        row, col = row_f - 0.5, col_f - 0.5
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
        self, coords: list[tuple[float, float]]
    ) -> list[Optional[float]]:
        return [self.sample(lat, lon) for lat, lon in coords]
