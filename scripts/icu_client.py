"""Minimal intervals.icu REST client (personal API key, athlete 0 = the key's owner).

The key comes from $ICU_API_KEY, else the macOS keychain item written with:
    security add-generic-password -U -a intervals.icu -s cycling-coach-intervals-icu -w
API reference: https://intervals.icu/api/v1/docs
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://intervals.icu/api/v1"
KEYCHAIN_ACCOUNT = "intervals.icu"
KEYCHAIN_SERVICE = "cycling-coach-intervals-icu"
TIMEOUT_S = 60


def api_key() -> str:
    key = os.environ.get("ICU_API_KEY")
    if key:
        return key
    out = subprocess.run(  # noqa: S603 — fixed argv, absolute path, no shell, no user input
        ["/usr/bin/security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit("No intervals.icu API key: set ICU_API_KEY or add it to the keychain "
                         f"(service {KEYCHAIN_SERVICE}).")
    return out.stdout.strip()


class ICUClient:
    def __init__(self, key: str | None = None, athlete: str = "0", min_interval_s: float = 0.5,
                 session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.auth = ("API_KEY", key or api_key())
        self.athlete = athlete
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def _req(self, method: str, path: str, *, athlete_scoped: bool = True, **kw: Any) -> requests.Response:
        """One request, throttled, with backoff on 429/5xx (up to 4 attempts).
        Paths are under /athlete/<id> unless athlete_scoped=False (e.g. /activity/<id>)."""
        url = f"{BASE_URL}/athlete/{self.athlete}{path}" if athlete_scoped else f"{BASE_URL}{path}"
        for attempt in range(4):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.s.request(method, url, timeout=TIMEOUT_S, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("Retry-After") or 5 * (attempt + 1)))
                continue
            return r
        return r

    def get(self, path: str, **params: Any) -> Any:
        r = self._req("GET", path, params=params)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: dict, **params: Any) -> Any:
        r = self._req("PUT", path, json=body, params=params)
        r.raise_for_status()
        return r.json()

    def activities(self, oldest: str, newest: str, fields: str | None = None) -> list[dict]:
        params = {"oldest": oldest, "newest": newest}
        if fields:
            params["fields"] = fields
        return self.get("/activities", **params)

    def update_activity(self, activity_id: str, body: dict) -> Any:
        r = self._req("PUT", f"/activity/{activity_id}", athlete_scoped=False, json=body)
        r.raise_for_status()
        return r.json()

    def create_manual_activities(self, items: list[dict]) -> Any:
        """Bulk create, upserting on external_id so a re-run updates instead of duplicating."""
        r = self._req("POST", "/activities/manual/bulk", json=items)
        r.raise_for_status()
        return r.json()

    def upload_activity(self, path: Path, name: str | None = None, external_id: str | None = None) -> requests.Response:
        """POST a fit/tcx/gpx (optionally .gz or .zip) file; returns the raw response for the caller to log."""
        params = {k: v for k, v in (("name", name), ("external_id", external_id)) if v}
        data = path.read_bytes()   # bytes, not a file handle, so a retried POST resends the whole file
        return self._req("POST", "/activities", params=params, files={"file": (path.name, data)})
