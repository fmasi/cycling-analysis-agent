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
        self._last_call: float = 0.0

    @property
    def configured(self) -> bool:
        return self._key is not None

    def sample_polyline(self, coords: list[tuple[float, float]]) -> list[float]:
        if not self.configured:
            raise RuntimeError(
                f"GPXZ key not configured. Place a key in {self.key_path}."
            )
        out: list[float] = []
        for i in range(0, len(coords), BATCH_SIZE):
            chunk = coords[i : i + BATCH_SIZE]
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            latlons = "|".join(f"{lat},{lon}" for lat, lon in chunk)
            r = requests.post(
                API_URL,
                data={"latlons": latlons},
                headers={"x-api-key": self._key},
                timeout=30,
            )
            self._last_call = time.monotonic()
            if r.status_code != 200:
                raise RuntimeError(f"GPXZ HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            out.extend(p["elevation"] for p in data["results"])
        return out
