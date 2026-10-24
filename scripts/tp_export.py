"""Export the full TrainingPeaks history to local, gitignored archives.

Run before a Premium subscription lapses: Basic hides the PMC and Metrics
charts, and TP's own export tool is limited to 12-month windows. This walks the
internal endpoints the TrainingPeaks MCP uses, through the `tp-mcp` package's
TPClient (keychain cookie, token exchange, request throttle). Read-only: every
call is a GET, except the PMC query, which TP serves as a POST.

Writes, relative to --out-root (default: the repo root):
    notes/tp-archive/fitness/performancedata.json   raw daily PMC series
    notes/tp-archive/fitness/pmc-daily.csv          date, tss, ctl, atl, tsb (TP's lag-1 TSB)
    notes/tp-archive/metrics/consolidatedtimedmetrics.json
    body-comp/tp-metrics-history.csv                one row per logged value (weight, injury, ...)
    notes/tp-archive/workouts/<year>.json           workout cards, incl. comments and structure
    notes/tp-archive/details/<year>/<workoutId>.json  file infos, mean-max curves, time in zones
    rides/fit/tp-export/<year>/<day>_<workoutId>_<fileId>_<name>  original device files
    rides/fit/tp-export/manifest.csv                workout_id, file_id, sha256, bytes, path
    notes/tp-archive/extras/*.json                  settings, equipment, libraries, events, ATP, notes

Resumable: details and files already on disk are skipped, so a re-run fetches
only what is new or failed. Exits non-zero if any request failed.

Usage:
    python scripts/tp_export.py                               # everything since --since
    python scripts/tp_export.py --since 2026-09-01 --limit 5  # quick test
    python scripts/tp_export.py --phases fitness,metrics
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PHASES = ("fitness", "metrics", "workouts", "files", "extras")
DEFAULT_SINCE = date(2015, 1, 1)
MAX_WINDOW_DAYS = 365          # the workouts list accepts a 12-month window
RETRY_CODES = {"RATE_LIMITED", "API_ERROR", "NETWORK_ERROR"}
MANIFEST_FIELDS = ["workout_id", "file_id", "workout_day", "sha256", "bytes", "path"]


# ---------------------------------------------------------------- pure helpers

def date_windows(since: date, until: date, max_days: int = MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Consecutive inclusive (start, end) windows covering since..until, each ≤ max_days long."""
    if until < since:
        return []
    out = []
    start = since
    while start <= until:
        end = min(start + timedelta(days=max_days - 1), until)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def flatten_pmc(rows: list[dict]) -> list[dict]:
    """TP performancedata rows -> date, tss, ctl, atl, tsb. TP's `tsb` is already lag-1."""
    out = []
    for r in rows:
        out.append({
            "date": (r.get("workoutDay") or "")[:10],
            "tss_actual": r.get("tssActual") or 0.0,
            "tss_planned": r.get("tssPlanned") or 0.0,
            "ctl": r.get("ctl"),
            "atl": r.get("atl"),
            "tsb": r.get("tsb"),
        })
    return out


def flatten_metrics(entries: list[dict]) -> list[dict]:
    """consolidatedtimedmetrics -> one row per logged value."""
    out = []
    for e in entries:
        for d in e.get("details") or []:
            out.append({
                "date": (e.get("timeStamp") or "")[:10],
                "time": d.get("time"),
                "type": d.get("type"),
                "label": d.get("label"),
                "value": d.get("value"),
                "source": d.get("uploadClient"),
            })
    return out


def is_completed(card: dict) -> bool:
    """A card with a recorded session (and so possibly a device file)."""
    return bool(card.get("completed")) or (card.get("totalTime") or 0) > 0


def safe_name(name: str) -> str:
    """Basename only, with anything outside [A-Za-z0-9._-] replaced: TP file names are untrusted."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "file"


def file_target(root: Path, card: dict, finfo: dict) -> Path:
    day = (card.get("workoutDay") or "0000-00-00")[:10]
    name = safe_name(finfo.get("fileName") or f"{finfo.get('fileId')}.fit.gz")
    return root / day[:4] / f"{day}_{card['workoutId']}_{finfo['fileId']}_{name}"


def payload_sha256(data: bytes) -> str:
    """sha256 of the file payload, gunzipped when TP stored it gzipped, so dedup sees through .gz."""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {(r["workout_id"], r["file_id"]): r for r in csv.DictReader(f)}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- exporter

class Exporter:
    def __init__(self, client: Any, out_root: Path, since: date, until: date,
                 limit: int | None = None, retry_delay: float = 5.0):
        self.c = client
        self.root = out_root
        self.since, self.until = since, until
        self.limit = limit
        self.retry_delay = retry_delay
        self.archive = out_root / "notes" / "tp-archive"
        self.files_root = out_root / "rides" / "fit" / "tp-export"
        self.aid: int | None = None
        self.failures: list[str] = []
        self.stats: dict[str, int] = {}

    def _count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    async def _call(self, method: str, endpoint: str, **kw: Any) -> Any:
        """One request with up to 3 retries on rate-limit/transient errors. None on failure."""
        for attempt in range(4):
            resp = await getattr(self.c, method)(endpoint, **kw)
            if resp.success:
                return resp
            code = resp.error_code.value if resp.error_code else "API_ERROR"
            if code not in RETRY_CODES or attempt == 3:
                self.failures.append(f"{method.upper()} {endpoint}: {code} {resp.message}")
                return None
            await asyncio.sleep(self.retry_delay * (attempt + 1))
        return None

    async def fitness(self) -> None:
        # One call covers the whole range (11 years answer in under a second). TP seeds the
        # window from the full history, so ctl/atl on the first day are real values, not 0.
        r = await self._call("post", f"/fitness/v1/athletes/{self.aid}/reporting/performancedata/"
                             f"{self.since}/{self.until}",
                             json={"atlConstant": 7, "atlStart": 0, "ctlConstant": 42,
                                   "ctlStart": 0, "workoutTypes": []})
        if r is None:
            return
        rows = r.data if isinstance(r.data, list) else []
        write_json(self.archive / "fitness" / "performancedata.json", rows)
        write_csv(self.archive / "fitness" / "pmc-daily.csv", flatten_pmc(rows),
                  ["date", "tss_actual", "tss_planned", "ctl", "atl", "tsb"])
        self._count("pmc_days", len(rows))

    async def metrics(self) -> None:
        r = await self._call("get", f"/metrics/v3/athletes/{self.aid}/consolidatedtimedmetrics/"
                             f"{self.since}/{self.until}")
        if r is None:
            return
        entries = r.data if isinstance(r.data, list) else []
        write_json(self.archive / "metrics" / "consolidatedtimedmetrics.json", entries)
        rows = flatten_metrics(entries)
        write_csv(self.root / "body-comp" / "tp-metrics-history.csv", rows,
                  ["date", "time", "type", "label", "value", "source"])
        self._count("metric_values", len(rows))

    async def workouts(self) -> list[dict]:
        """Workout cards per window, merged into one JSON file per year."""
        by_year: dict[str, list[dict]] = {}
        for start, end in date_windows(self.since, self.until):
            r = await self._call("get", f"/fitness/v6/athletes/{self.aid}/workouts/{start}/{end}")
            for card in (r.data if r is not None and isinstance(r.data, list) else []):
                by_year.setdefault((card.get("workoutDay") or "0000")[:4], []).append(card)
        cards = []
        for year, items in sorted(by_year.items()):
            items.sort(key=lambda c: (c.get("workoutDay") or "", c.get("orderOnDay") or 0))
            write_json(self.archive / "workouts" / f"{year}.json", items)
            cards.extend(items)
        self._count("cards", len(cards))
        self._count("cards_completed", sum(is_completed(c) for c in cards))
        return cards

    def _cached_cards(self) -> list[dict]:
        cards = []
        for p in sorted((self.archive / "workouts").glob("*.json")):
            cards.extend(json.loads(p.read_text()))
        return [c for c in cards if self.since.isoformat() <= (c.get("workoutDay") or "")[:10]
                <= self.until.isoformat()]

    async def details(self, card: dict) -> dict | None:
        day = (card.get("workoutDay") or "0000")[:10]
        path = self.archive / "details" / day[:4] / f"{card['workoutId']}.json"
        if path.exists():
            self._count("details_cached")
            return json.loads(path.read_text())
        r = await self._call("get", f"/fitness/v6/athletes/{self.aid}/workouts/{card['workoutId']}/details")
        if r is None or not isinstance(r.data, dict):
            return None
        write_json(path, r.data)
        self._count("details_fetched")
        return r.data

    async def files(self, cards: list[dict]) -> None:
        manifest_path = self.files_root / "manifest.csv"
        manifest = load_manifest(manifest_path)
        seen_hashes = {row["sha256"] for row in manifest.values()}
        done = [c for c in cards if is_completed(c)]
        if self.limit is not None:
            done = done[-self.limit:]
        for card in done:
            det = await self.details(card)
            for finfo in (det or {}).get("workoutDeviceFileInfos") or []:
                key = (str(card["workoutId"]), str(finfo["fileId"]))
                target = file_target(self.files_root, card, finfo)
                if key in manifest and target.exists():
                    self._count("files_cached")
                    continue
                raw = await self._call("get_raw", f"/fitness/v6/athletes/{self.aid}/workouts/"
                                       f"{card['workoutId']}/rawfiledata/{finfo['fileId']}")
                if raw is None or not raw.content:
                    continue
                digest = payload_sha256(raw.content)
                if digest in seen_hashes:
                    self._count("files_duplicate")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw.content)
                seen_hashes.add(digest)
                manifest[key] = {"workout_id": key[0], "file_id": key[1],
                                 "workout_day": (card.get("workoutDay") or "")[:10],
                                 "sha256": digest, "bytes": len(raw.content),
                                 "path": str(target.relative_to(self.root))}
                self._count("files_downloaded")
            # Persist after every workout so an interrupted run resumes cleanly.
            write_csv(manifest_path, sorted(manifest.values(), key=lambda r: (r["workout_day"], r["path"])),
                      MANIFEST_FIELDS)

    async def extras(self) -> None:
        a, s, u = self.aid, self.since, self.until
        single = {
            "settings": f"/fitness/v1/athletes/{a}/settings",
            "poollengthsettings": f"/fitness/v1/athletes/{a}/poollengthsettings",
            "equipment": f"/fitness/v1/athletes/{a}/equipment",
            "atp": f"/fitness/v1/athletes/{a}/atp/{s}/{u}",
        }
        for name, ep in single.items():
            r = await self._call("get", ep)
            if r is not None:
                write_json(self.archive / "extras" / f"{name}.json", r.data)
                self._count("extras")
        ranged = {
            "events": f"/fitness/v6/athletes/{a}/events/{{start}}/{{end}}",
            "calendar-notes": f"/fitness/v2/athletes/{a}/calendarNote/{{start}}/{{end}}",
            "availability": f"/fitness/v1/athletes/{a}/availability/{{start}}/{{end}}",
        }
        for name, tmpl in ranged.items():
            items: list[Any] = []
            for start, end in date_windows(s, u):
                r = await self._call("get", tmpl.format(start=start, end=end))
                if r is not None and isinstance(r.data, list):
                    items.extend(r.data)
            write_json(self.archive / "extras" / f"{name}.json", items)
            self._count("extras")
        r = await self._call("get", "/exerciselibrary/v2/libraries")
        libraries = r.data if r is not None and isinstance(r.data, list) else []
        for lib in libraries:
            lib_id = lib.get("exerciseLibraryId") or lib.get("id")
            # Only the athlete's own libraries: TP's shared default content isn't theirs to archive.
            if lib_id is None or lib.get("ownerId") != a:
                continue
            items_r = await self._call("get", f"/exerciselibrary/v2/libraries/{lib_id}/items")
            lib["items"] = items_r.data if items_r is not None else None
        write_json(self.archive / "extras" / "libraries.json", libraries)
        self._count("extras")

    async def run(self, phases: list[str]) -> int:
        self.aid = await self.c.ensure_athlete_id()
        if not self.aid:
            print("Could not resolve the athlete ID: re-authenticate with `tp-mcp auth`.", file=sys.stderr)
            return 2
        if "fitness" in phases:
            await self.fitness()
        if "metrics" in phases:
            await self.metrics()
        cards = await self.workouts() if "workouts" in phases else self._cached_cards()
        if "files" in phases:
            await self.files(cards)
        if "extras" in phases:
            await self.extras()
        for k, v in sorted(self.stats.items()):
            print(f"  {k}: {v}")
        for f in self.failures:
            print(f"  FAILED {f}", file=sys.stderr)
        return 1 if self.failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export the full TrainingPeaks history to local archives.")
    p.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE,
                   help=f"first day to export (default {DEFAULT_SINCE}; earlier days are just empty)")
    p.add_argument("--until", type=date.fromisoformat, default=date.today())
    p.add_argument("--phases", default=",".join(PHASES),
                   help=f"comma-separated subset of {','.join(PHASES)}")
    p.add_argument("--limit", type=int, default=None,
                   help="files phase: only the N most recent completed workouts (for testing)")
    p.add_argument("--out-root", type=Path, default=REPO_ROOT)
    args = p.parse_args(argv)
    args.phases = [x.strip() for x in args.phases.split(",") if x.strip()]
    bad = set(args.phases) - set(PHASES)
    if bad:
        p.error(f"unknown phase(s): {', '.join(sorted(bad))}")
    return args


async def _main(args: argparse.Namespace) -> int:
    from tp_mcp.client.http import TPClient  # deferred: tests run without tp-mcp

    async with TPClient() as client:
        exporter = Exporter(client, args.out_root, args.since, args.until, limit=args.limit)
        return await exporter.run(args.phases)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(parse_args())))
