"""Map-match GPX coordinates to road centrelines via OSRM.

The climb verifier densifies between GPX waypoints with straight geodesic
lines, which wanders off the road on curves and switchbacks. OSRM's match
service snaps a noisy GPS trace to the underlying road network and returns
a dense LineString that follows actual road geometry — eliminating the
~±2-3pp peak-25m uncertainty caused by straight-line densification.

Default endpoint is the OSRM Project demo server, which is rate-limited
and meant for testing. For production / heavy use, run a local OSRM
container and override OSRM_URL.

Results are sha256-cached under ~/.cache/cycling-coach/osrm so re-running
the verifier on the same route hits zero network.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Optional

import requests


OSRM_URL = os.environ.get(
    "OSRM_URL", "https://router.project-osrm.org/match/v1/cycling"
)
CACHE_DIR = Path.home() / ".cache" / "cycling-coach" / "osrm"

# The OSRM project demo server caps the /match endpoint at 10 coordinates
# per request (the /route endpoint allows ~100 — different limit). Subsample
# the GPX waypoints down to this cap; OSRM expands to the full road geometry
# between them via the HMM matcher.
MAX_COORDS_PER_REQUEST = 10


def _cache_key(coords: list[tuple[float, float]]) -> str:
    rounded = [(round(la, 6), round(lo, 6)) for la, lo in coords]
    blob = json.dumps(rounded).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _subsample(coords: list, max_n: int) -> list:
    """Reduce to ≤max_n points, preserving first and last."""
    n = len(coords)
    if n <= max_n:
        return list(coords)
    step = (n - 1) / (max_n - 1)
    idx = {int(round(i * step)) for i in range(max_n)}
    idx.add(0)
    idx.add(n - 1)
    return [coords[i] for i in sorted(idx)]


def match_coords(
    coords: list[tuple[float, float]],
    *,
    timeout: int = 30,
    radius_m: float = 25.0,
    cache_dir: Optional[Path] = None,
) -> list[tuple[float, float]]:
    """Snap a GPX trace to OSRM road geometry. Returns (lat, lon) list.

    On any error (network, OSRM rejection, malformed response) the original
    coords are returned unchanged — so callers see this as a free upgrade
    when available and a no-op when not.
    """
    if len(coords) < 2:
        return list(coords)
    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(coords)
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            return [tuple(p) for p in json.loads(cache_path.read_text())]
        except Exception:
            cache_path.unlink(missing_ok=True)

    sample = _subsample(coords, max_n=MAX_COORDS_PER_REQUEST)
    coord_str = ";".join(f"{lo:.6f},{la:.6f}" for la, lo in sample)
    radii = ";".join(f"{radius_m:.1f}" for _ in sample)
    url = (
        f"{OSRM_URL}/{coord_str}"
        f"?geometries=geojson&overview=full&tidy=true&gaps=ignore"
        f"&radiuses={radii}"
    )
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return list(coords)
        data = r.json()
        if data.get("code") != "Ok" or not data.get("matchings"):
            return list(coords)
        out: list[tuple[float, float]] = []
        for m in data["matchings"]:
            for lon, lat in m["geometry"]["coordinates"]:
                if not out or (out[-1] != (float(lat), float(lon))):
                    out.append((float(lat), float(lon)))
        if not out:
            return list(coords)
        cache_path.write_text(json.dumps(out))
        return out
    except Exception:
        return list(coords)
