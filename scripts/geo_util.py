"""Shared geographic helpers: great-circle distance + GPX bounding box.

Previously duplicated across analyse_gpx/verify_climbs (haversine) and
fetch_dem_tiles/make_dem_shapefile (bbox). Centralised so the geometry is
defined once.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bbox_from_gpx(path: Path) -> tuple[float, float, float, float]:
    """Return (min_lon, min_lat, max_lon, max_lat) over all trackpoints."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    ns = {"g": root.tag.split("}")[0].strip("{")}
    pts = root.findall(".//g:trkpt", ns)
    if not pts:
        raise ValueError(f"No trackpoints in {path}")
    lats = [float(p.attrib["lat"]) for p in pts]
    lons = [float(p.attrib["lon"]) for p in pts]
    return (min(lons), min(lats), max(lons), max(lats))
