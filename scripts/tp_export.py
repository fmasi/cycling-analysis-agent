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
    notes/tp-archive/attachments/<year>/...         files attached to workouts (photos, PDFs)
    rides/fit/tp-export/manifest.csv                kind, workout_id, file_id, sha256, bytes, path
    notes/tp-archive/extras/*.json                  settings, equipment, libraries, events, ATP, notes,
                                                    plans, personal records, private workout notes
    notes/tp-archive/strength/<year>/<id>.json      strength-builder workouts (a separate TP API:
                                                    they never appear among the workout cards)

A narrower --since/--until run MERGES into the existing archives (by date or
id; the newer copy wins), so a final top-up never truncates the history.
Resumable: details and files already on disk are skipped, except details of
workouts in the last --refresh-days (and, on a narrower --since run, everything
from --since on), which are re-fetched to catch late uploads and edits.
Writes are atomic. Stops on the first auth failure; exits non-zero if any
request failed.

Usage:
    python scripts/tp_export.py                               # everything since --since
    python scripts/tp_export.py --since 2026-10-20            # top-up, merged into the archive
    python scripts/tp_export.py --since 2026-09-01 --limit 5 --out-root /tmp/tp-test  # quick test
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PHASES = ("fitness", "metrics", "workouts", "files", "strength", "extras")
DEFAULT_SINCE = date(2015, 1, 1)
MAX_WINDOW_DAYS = 365          # the workouts list accepts a 12-month window
RETRY_CODES = {"RATE_LIMITED", "API_ERROR", "NETWORK_ERROR"}
# A 401 (AUTH_EXPIRED) or missing credentials end the run. A 403 also maps to AUTH_INVALID in
# tp-mcp, but it means "not allowed here" (e.g. a Premium-only endpoint), so it fails only that call.
FORBIDDEN_PREFIX = "Access denied"
MANIFEST_FIELDS = ["kind", "workout_id", "file_id", "workout_day", "sha256", "bytes", "path"]
SESSION_FIELDS = ("totalTime", "distance", "tssActual", "startTime", "heartRateAverage", "powerAverage")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class AuthAbort(Exception):
    """The TP session died mid-run: stop instead of hammering the token endpoint."""


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
    return [{
        "date": (r.get("workoutDay") or "")[:10],
        "tss_actual": r.get("tssActual") or 0.0,
        "tss_planned": r.get("tssPlanned") or 0.0,
        "ctl": r.get("ctl"),
        "atl": r.get("atl"),
        "tsb": r.get("tsb"),
    } for r in rows]


def flatten_metrics(entries: list[dict]) -> list[dict]:
    """consolidatedtimedmetrics -> one row per logged value."""
    return [{
        "date": (e.get("timeStamp") or "")[:10],
        "time": d.get("time"),
        "type": d.get("type"),
        "label": d.get("label"),
        "value": d.get("value"),
        "source": d.get("uploadClient"),
    } for e in entries for d in e.get("details") or []]


def merge_by_key(old: list, new: list, key: Callable[[Any], Any]) -> list:
    """Union of old and new keyed by key(item); the new copy wins; order follows the key."""
    merged = {key(x): x for x in old}
    merged.update({key(x): x for x in new})
    return [merged[k] for k in sorted(merged, key=str)]


def item_key(x: Any) -> str:
    """Stable identity for extras rows: their id when they have one, else the whole row."""
    if isinstance(x, dict):
        for k in ("id", "eventId", "calendarNoteId", "availabilityId"):
            if x.get(k) is not None:
                return f"{k}:{x[k]}"
    return json.dumps(x, sort_keys=True)


def has_session(card: dict) -> bool:
    """A card with a recorded session, so possibly device files. Planned-only cards have none of these."""
    return bool(card.get("completed")) or any(card.get(k) not in (None, 0, "") for k in SESSION_FIELDS)


def safe_name(name: str) -> str:
    """Basename only, with anything outside [A-Za-z0-9._-] replaced: TP file names are untrusted."""
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "file"


def card_day(card: dict) -> str:
    day = (card.get("workoutDay") or "")[:10]
    return day if DAY_RE.fullmatch(day) else "0000-00-00"


def file_target(root: Path, card: dict, finfo: dict) -> Path:
    """<root>/<year>/<day>_<workoutId>_<fileId>_<name>, guaranteed to stay under root."""
    day = card_day(card)
    wid, fid = int(card["workoutId"]), int(finfo["fileId"])
    name = safe_name(finfo.get("fileName") or f"{fid}.fit.gz")
    target = root / day[:4] / f"{day}_{wid}_{fid}_{name}"
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"refusing to write outside {root}: {target}")
    return target


def payload_sha256(data: bytes) -> str:
    """sha256 of the file payload, gunzipped when TP stored it gzipped, so dedup sees through .gz."""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temp file and rename, so an interrupted run never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_json(path: Path, data: Any) -> None:
    write_atomic(path, json.dumps(data, indent=1, ensure_ascii=False).encode("utf-8"))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    write_atomic(path, buf.getvalue().encode("utf-8"))


def read_json(path: Path, default: Any) -> Any:
    """Parsed JSON, or default when the file is missing or unreadable (e.g. a pre-atomic truncation)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_list(path: Path) -> list:
    """An archive file expected to hold a JSON list; anything else (missing, truncated,
    or an older single-object format) counts as empty so a merge can't misread it."""
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def load_manifest(path: Path) -> dict[tuple[str, str, str], dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["kind"] = r.get("kind") or "device"
    return {(r["kind"], r["workout_id"], r["file_id"]): r for r in rows}


# ---------------------------------------------------------------- exporter

class Exporter:
    def __init__(self, client: Any, out_root: Path, since: date, until: date,
                 limit: int | None = None, refresh_days: int = 14, retry_delay: float = 5.0,
                 strength_get: Callable[[str], Any] | None = None,
                 pr_types: dict[str, list[str]] | None = None):
        self.c = client
        self.root = out_root
        self.since, self.until = since, until
        self.limit = limit
        self.refresh_from = (until - timedelta(days=refresh_days)).isoformat()
        if since != DEFAULT_SINCE:   # a top-up run re-checks everything it was asked to cover
            self.refresh_from = min(self.refresh_from, since.isoformat())
        self.strength_get = strength_get   # async (path) -> (status, json); None skips the phase
        self.pr_types = pr_types or {}     # {"Bike": [...], "Run": [...]}; empty skips PR lists
        self.retry_delay = retry_delay
        self.archive = out_root / "notes" / "tp-archive"
        self.files_root = out_root / "rides" / "fit" / "tp-export"
        self.aid: int | None = None
        self.failures: list[str] = []
        self.stats: dict[str, int] = {}

    def _count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def _fail(self, msg: str) -> None:
        self.failures.append(msg)

    async def _call(self, method: str, endpoint: str, *, forbidden_ok: str | None = None, **kw: Any) -> Any:
        """One request with up to 3 retries on rate-limit/transient errors. None on failure.
        forbidden_ok: a stats key; a 403 on this call is then counted there instead of failing
        (for content TP doesn't let the athlete read, e.g. the workouts of a bought store plan)."""
        for attempt in range(4):
            resp = await getattr(self.c, method)(endpoint, **kw)
            if resp.success:
                return resp
            code = resp.error_code.value if resp.error_code else "API_ERROR"
            if code == "AUTH_EXPIRED" or (code == "AUTH_INVALID" and not resp.message.startswith(FORBIDDEN_PREFIX)):
                raise AuthAbort(f"{method.upper()} {endpoint}: {code} {resp.message}")
            if forbidden_ok and code == "AUTH_INVALID":
                self._count(forbidden_ok)
                return None
            if code not in RETRY_CODES or attempt == 3:
                self._fail(f"{method.upper()} {endpoint}: {code} {resp.message}")
                return None
            await asyncio.sleep(self.retry_delay * (attempt + 1))
        return None

    def _list(self, r: Any, what: str) -> list | None:
        """The response's list payload; a 200 with anything else is a failure, not "no rows"."""
        if r is None:
            return None
        if isinstance(r.data, list):
            return r.data
        self._fail(f"{what}: expected a list, got {type(r.data).__name__}")
        return None

    async def fitness(self) -> None:
        # One call covers the whole range (11 years answer in under a second). TP seeds the
        # window from the full history, so ctl/atl on the first day are real values, not 0.
        r = await self._call("post", f"/fitness/v1/athletes/{self.aid}/reporting/performancedata/"
                             f"{self.since}/{self.until}",
                             json={"atlConstant": 7, "atlStart": 0, "ctlConstant": 42,
                                   "ctlStart": 0, "workoutTypes": []})
        new = self._list(r, "performancedata")
        if new is None:
            return
        path = self.archive / "fitness" / "performancedata.json"
        rows = merge_by_key(read_list(path), new,
                            lambda x: (x.get("workoutDay") or "")[:10])
        write_json(path, rows)
        write_csv(self.archive / "fitness" / "pmc-daily.csv", flatten_pmc(rows),
                  ["date", "tss_actual", "tss_planned", "ctl", "atl", "tsb"])
        self._count("pmc_days", len(rows))

    async def metrics(self) -> None:
        r = await self._call("get", f"/metrics/v3/athletes/{self.aid}/consolidatedtimedmetrics/"
                             f"{self.since}/{self.until}")
        new = self._list(r, "consolidatedtimedmetrics")
        if new is None:
            return
        path = self.archive / "metrics" / "consolidatedtimedmetrics.json"
        entries = merge_by_key(read_list(path), new,
                               lambda x: (x.get("timeStamp") or "")[:10] + f"#{x.get('id')}")
        write_json(path, entries)
        rows = flatten_metrics(entries)
        write_csv(self.root / "body-comp" / "tp-metrics-history.csv", rows,
                  ["date", "time", "type", "label", "value", "source"])
        self._count("metric_values", len(rows))

    async def workouts(self) -> list[dict]:
        """Workout cards per window, merged by workoutId into one JSON file per year."""
        by_year: dict[str, list[dict]] = {}
        for start, end in date_windows(self.since, self.until):
            r = await self._call("get", f"/fitness/v6/athletes/{self.aid}/workouts/{start}/{end}")
            for card in self._list(r, f"workouts {start}..{end}") or []:
                by_year.setdefault(card_day(card)[:4], []).append(card)
        new_year = {c["workoutId"]: y for y, items in by_year.items() for c in items}
        # A card moved to another year in TP: drop its stale copy from the old year's file.
        for path in sorted((self.archive / "workouts").glob("*.json")):
            old = read_list(path)
            kept = [c for c in old if new_year.get(c.get("workoutId"), path.stem) == path.stem]
            if len(kept) != len(old):
                write_json(path, kept)
                self._count("cards_moved_year", len(old) - len(kept))
        for year, items in sorted(by_year.items()):
            path = self.archive / "workouts" / f"{year}.json"
            merged = merge_by_key(read_list(path), items, lambda c: c["workoutId"])
            merged.sort(key=lambda c: (card_day(c), c.get("orderOnDay") or 0))
            write_json(path, merged)
        cards = self._cached_cards()
        self._count("cards", len(cards))
        self._count("cards_with_session", sum(has_session(c) for c in cards))
        return cards

    def _cached_cards(self) -> list[dict]:
        cards = []
        for p in sorted((self.archive / "workouts").glob("*.json")):
            cards.extend(read_list(p))
        lo, hi = self.since.isoformat(), self.until.isoformat()
        return [c for c in cards if lo <= card_day(c) <= hi]

    async def details(self, card: dict) -> dict | None:
        day = card_day(card)
        path = self.archive / "details" / day[:4] / f"{int(card['workoutId'])}.json"
        cached = read_json(path, None) if day < self.refresh_from else None
        if isinstance(cached, dict):
            self._count("details_cached")
            return cached
        r = await self._call("get", f"/fitness/v6/athletes/{self.aid}/workouts/{card['workoutId']}/details")
        if r is None:
            return None
        if not isinstance(r.data, dict):
            self._fail(f"details {card['workoutId']}: unexpected payload {type(r.data).__name__}")
            return None
        write_json(path, r.data)
        self._count("details_fetched")
        return r.data

    async def files(self, cards: list[dict]) -> None:
        if not cards:
            self._fail("files: no workout cards in range; run the workouts phase first")
            return
        manifest_path = self.files_root / "manifest.csv"
        manifest = load_manifest(manifest_path)
        seen_hashes = {row["sha256"] for row in manifest.values()}
        sessions = [c for c in cards if has_session(c)]
        self._count("cards_planned_only", len(cards) - len(sessions))
        if self.limit:
            sessions = sessions[-self.limit:]
        kinds = (("device", "workoutDeviceFileInfos", self.files_root),
                 ("attachment", "attachmentFileInfos", self.archive / "attachments"))
        for card in sessions:
            det = await self.details(card) or {}
            for kind, field, root in kinds:
                for finfo in det.get(field) or []:
                    if finfo.get("fileId") is None:
                        self._fail(f"{kind} file without fileId on workout {card['workoutId']}")
                        continue
                    key = (kind, str(card["workoutId"]), str(finfo["fileId"]))
                    target = file_target(root, card, finfo)
                    if key in manifest and target.exists():
                        self._count(f"{kind}_cached")
                        continue
                    raw = await self._call("get_raw", f"/fitness/v6/athletes/{self.aid}/workouts/"
                                           f"{card['workoutId']}/rawfiledata/{finfo['fileId']}")
                    if raw is None:
                        continue
                    if not raw.content:
                        self._fail(f"{kind} {key[1]}/{key[2]}: empty file body")
                        continue
                    digest = payload_sha256(raw.content)
                    if digest in seen_hashes:
                        self._count(f"{kind}_duplicate_payload")
                    write_atomic(target, raw.content)
                    seen_hashes.add(digest)
                    manifest[key] = {"kind": kind, "workout_id": key[1], "file_id": key[2],
                                     "workout_day": card_day(card), "sha256": digest,
                                     "bytes": len(raw.content), "path": str(target.relative_to(self.root))}
                    self._count(f"{kind}_downloaded")
            # Persist after every workout so an interrupted run resumes cleanly.
            write_csv(manifest_path, sorted(manifest.values(), key=lambda r: (r["workout_day"], r["path"])),
                      MANIFEST_FIELDS)

    async def extras(self) -> None:
        a, s, u = self.aid, self.since, self.until
        single = {
            "settings": f"/fitness/v1/athletes/{a}/settings",
            "poollengthsettings": f"/fitness/v1/athletes/{a}/poollengthsettings",
            "equipment": f"/fitness/v1/athletes/{a}/equipment",
        }
        for name, ep in single.items():
            r = await self._call("get", ep)
            if r is not None:
                write_json(self.archive / "extras" / f"{name}.json", r.data)
                self._count("extras")
        ranged = {
            "atp": f"/fitness/v1/athletes/{a}/atp/{{start}}/{{end}}",
            "events": f"/fitness/v6/athletes/{a}/events/{{start}}/{{end}}",
            "calendar-notes": f"/fitness/v2/athletes/{a}/calendarNote/{{start}}/{{end}}",
            "availability": f"/fitness/v1/athletes/{a}/availability/{{start}}/{{end}}",
        }
        for name, tmpl in ranged.items():
            new: list[Any] = []
            for start, end in date_windows(s, u):
                r = await self._call("get", tmpl.format(start=start, end=end))
                if r is not None:   # ATP answers with a single object, the others with lists
                    new.extend(r.data if isinstance(r.data, list) else [r.data] if r.data else [])
            path = self.archive / "extras" / f"{name}.json"
            write_json(path, merge_by_key(read_list(path), new, item_key))
            self._count("extras")
        r = await self._call("get", "/exerciselibrary/v2/libraries")
        libraries = self._list(r, "libraries")
        if libraries is None:
            return                         # keep the archived copy rather than overwrite it with nothing
        previous = {(x.get("exerciseLibraryId") or x.get("id")): x
                    for x in read_list(self.archive / "extras" / "libraries.json")}
        for lib in libraries:
            lib_id = lib.get("exerciseLibraryId") or lib.get("id")
            # Only the athlete's own libraries: TP's shared default content isn't theirs to archive.
            if lib_id is None or lib.get("ownerId") != a:
                continue
            items_r = await self._call("get", f"/exerciselibrary/v2/libraries/{lib_id}/items")
            lib["items"] = items_r.data if items_r is not None else (previous.get(lib_id) or {}).get("items")
        write_json(self.archive / "extras" / "libraries.json", libraries)
        self._count("extras")
        await self.plans()
        await self.personal_records()
        await self.private_notes()

    async def plans(self) -> None:
        """Training plans the athlete owns or bought, each with its workouts."""
        r = await self._call("get", "/plans/v1/plans")
        plans = self._list(r, "plans")
        if plans is None:
            return
        out = []
        for plan in plans:
            pid = plan.get("planId") or plan.get("id")
            det = await self._call("get", f"/plans/v1/plans/{pid}") if pid is not None else None
            d = det.data if det is not None and isinstance(det.data, dict) else {}
            entry = {"plan": plan, "detail": d, "workouts": None}
            start, days = (d.get("startDate") or "")[:10], d.get("dayCount") or (d.get("weekCount") or 0) * 7
            if DAY_RE.fullmatch(start) and days:
                end = date.fromisoformat(start) + timedelta(days=int(days) + 1)
                # Bought store plans answer 403 here; the sessions done from them are workout cards anyway.
                wr = await self._call("get", f"/plans/v1/plans/{pid}/workouts/{start}/{end}",
                                      forbidden_ok="plan_workouts_not_readable")
                entry["workouts"] = wr.data if wr is not None else None
            out.append(entry)
        write_json(self.archive / "extras" / "plans.json", out)
        self._count("plans", len(out))

    async def personal_records(self) -> None:
        """PR lists per sport and type over the whole range, plus each workout's own PRs."""
        lists: dict[str, Any] = {}
        for sport, types in self.pr_types.items():
            for pr in types:
                r = await self._call("get", f"/personalrecord/v2/athletes/{self.aid}/{sport}",
                                     params={"prType": pr, "startDate": f"{self.since}T00:00:00",
                                             "endDate": f"{self.until}T00:00:00"})
                if r is not None:
                    lists[f"{sport}/{pr}"] = r.data
        if lists:
            path = self.archive / "extras" / "personal-records.json"
            prev = read_json(path, {})
            write_json(path, {**(prev if isinstance(prev, dict) else {}), **lists})
        per_workout_path = self.archive / "extras" / "workout-prs.json"
        per_workout = read_json(per_workout_path, {})
        per_workout = per_workout if isinstance(per_workout, dict) else {}
        for c in self._cached_cards():
            if (c.get("personalRecordCount") or 0) > 0:
                wid = str(c["workoutId"])
                if wid in per_workout and card_day(c) < self.refresh_from:
                    continue
                r = await self._call("get", f"/personalrecord/v2/athletes/{self.aid}/workouts/{wid}",
                                     params={"displayPeaksForBasic": "true"})
                if r is not None:
                    per_workout[wid] = r.data
        write_json(per_workout_path, per_workout)
        self._count("workouts_with_prs", len(per_workout))

    async def private_notes(self) -> None:
        path = self.archive / "extras" / "private-workout-notes.json"
        notes = read_json(path, {})
        notes = notes if isinstance(notes, dict) else {}
        for c in self._cached_cards():
            if c.get("hasPrivateWorkoutNoteForCaller"):
                r = await self._call("get", f"/fitness/v6/workouts/{c['workoutId']}/privateWorkoutNote")
                if r is not None:
                    notes[str(c["workoutId"])] = r.data
        write_json(path, notes)
        self._count("private_notes", len(notes))

    async def strength(self) -> None:
        """Strength-builder workouts live on TP's separate strength API and never appear as workout
        cards, yet their TSS is in the PMC. Archive the list and each workout's detail and summary."""
        if self.strength_get is None:
            return
        listing: list[dict] = []
        for start, end in date_windows(self.since, self.until):
            status, body = await self.strength_get(f"/rx/activity/v1/workouts/calendar/{self.aid}/{start}/{end}")
            if status != 200:
                self._fail(f"strength list {start}..{end}: HTTP {status}")
                continue
            items = body if isinstance(body, list) else (body or {}).get("data")
            if not isinstance(items, list):
                self._fail(f"strength list {start}..{end}: expected a list")
                continue
            listing.extend(items)
        list_path = self.archive / "strength" / "list.json"
        write_json(list_path, merge_by_key(read_list(list_path), listing, lambda w: str(w.get("id"))))
        for w in listing:
            day = (w.get("prescribedDate") or w.get("startDateTime") or "")[:10]
            day = day if DAY_RE.fullmatch(day) else "0000-00-00"
            wid = int(w["id"])
            entry: dict[str, Any] = {"listing": w}
            for key, suffix in (("detail", ""), ("summary", "/summary")):
                status, body = await self.strength_get(f"/rx/activity/v1/workouts/{wid}{suffix}")
                if status != 200:
                    self._fail(f"strength {wid}{suffix}: HTTP {status}")
                entry[key] = (body or {}).get("data", body) if isinstance(body, dict) else body
            write_json(self.archive / "strength" / day[:4] / f"{wid}.json", entry)
            self._count("strength_workouts")

    async def run(self, phases: list[str]) -> int:
        self.aid = await self.c.ensure_athlete_id()
        if not self.aid:
            print("Could not resolve the athlete ID: re-authenticate with `tp-mcp auth`.", file=sys.stderr)
            return 2
        try:
            if "fitness" in phases:
                await self.fitness()
            if "metrics" in phases:
                await self.metrics()
            cards = await self.workouts() if "workouts" in phases else self._cached_cards()
            if "files" in phases:
                await self.files(cards)
            if "strength" in phases:
                await self.strength()
            if "extras" in phases:
                await self.extras()
        except AuthAbort as e:
            print(f"Auth failed, stopping (re-run after `tp-mcp auth` to resume): {e}", file=sys.stderr)
            return 2
        finally:
            for k, v in sorted(self.stats.items()):
                print(f"  {k}: {v}")
            for f in self.failures:
                print(f"  FAILED {f}", file=sys.stderr)
        return 1 if self.failures else 0


def positive_int(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be ≥ 1")
    return n


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Export the full TrainingPeaks history to local archives.")
    p.add_argument("--since", type=date.fromisoformat, default=DEFAULT_SINCE,
                   help=f"first day to export (default {DEFAULT_SINCE}; earlier days are just empty)")
    p.add_argument("--until", type=date.fromisoformat, default=date.today())
    p.add_argument("--phases", default=",".join(PHASES),
                   help=f"comma-separated subset of {','.join(PHASES)}")
    p.add_argument("--limit", type=positive_int, default=None,
                   help="files phase: only the N most recent workouts with a session (for testing)")
    p.add_argument("--refresh-days", type=int, default=14,
                   help="re-fetch details of workouts in the last N days to catch late uploads (default 14)")
    p.add_argument("--out-root", type=Path, default=REPO_ROOT)
    args = p.parse_args(argv)
    args.phases = [x.strip() for x in args.phases.split(",") if x.strip()]
    bad = set(args.phases) - set(PHASES)
    if bad:
        p.error(f"unknown phase(s): {', '.join(sorted(bad))}")
    return args


async def _main(args: argparse.Namespace) -> int:
    # Deferred: the tests run without tp-mcp. The strength helpers are tp-mcp internals
    # (pinned in environment.yml), since TPClient only talks to the main API host.
    import httpx
    from tp_mcp.client.http import TPClient
    from tp_mcp.tools.peaks import BIKE_PR_TYPES, RUN_PR_TYPES
    from tp_mcp.tools.strength import STRENGTH_API_BASE, _access, _headers

    async with TPClient() as client, httpx.AsyncClient(timeout=30) as h:
        async def strength_get(path: str) -> tuple[int, Any]:
            _, access, err = await _access(client)
            if err:
                raise AuthAbort(f"strength API: {err.get('message')}")
            r = await h.get(f"{STRENGTH_API_BASE}{path}", headers=_headers(access))
            if r.status_code == 401:
                raise AuthAbort(f"GET {path}: HTTP 401")
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None

        exporter = Exporter(client, args.out_root, args.since, args.until,
                            limit=args.limit, refresh_days=args.refresh_days, strength_get=strength_get,
                            pr_types={"Bike": list(BIKE_PR_TYPES), "Run": list(RUN_PR_TYPES)})
        return await exporter.run(args.phases)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(parse_args())))
