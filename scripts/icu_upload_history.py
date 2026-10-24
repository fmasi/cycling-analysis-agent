"""Upload the archived TrainingPeaks history to intervals.icu without creating duplicates.

Candidates are the original device files from `rides/fit/tp-export/manifest.csv`
(written by tp_export.py) plus any `rides/fit/*.fit` not already in it. Each
file's real start time and duration are read from the file itself. Then:

1. Same-session recordings (same kind of activity, overlapping by ≥ 50% of the
   shorter one), e.g. a ROAM ride also recorded on the Apple Watch, are grouped
   and only one is kept: the one with power, then by device (Wahoo > Garmin >
   other > Apple Watch), then the longest.
2. A kept file is skipped if it overlaps an activity already in intervals.icu
   (from any source: Wahoo sync, HealthFit, an earlier run).
3. Rides from --defer-rides-from onwards are held back while intervals.icu's own
   Wahoo sync is still backfilling them; re-run with --defer-rides-from none
   once that has finished, and step 2 skips whatever Wahoo already delivered.

Dry run by default: prints the plan and writes it to <log-dir>/icu-upload-plan.csv.
--apply uploads, logging every result to <log-dir>/icu-upload-log.csv; a re-run
never re-uploads a file the log marks as uploaded.

Usage:
    python scripts/icu_upload_history.py                       # dry run, full history
    python scripts/icu_upload_history.py --apply --until 2015-12-31
    python scripts/icu_upload_history.py --apply --defer-rides-from none
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVICE_RANK = {"wahoo": 0, "garmin": 1, "other": 2, "applewatch": 3}   # lower wins
OVERLAP_FRACTION = 0.5
RIDE_SPORTS = {"cycling", "biking", "ride", "e_biking", "virtualride", "e_bike_ride", "gravelride",
               "mountainbikeride", "ebikeride"}
RUN_SPORTS = {"running", "run", "virtualrun", "trailrun"}
PLAN_FIELDS = ["action", "reason", "start_utc", "duration_s", "sport", "device", "has_power",
               "workout_id", "file_id", "title", "path", "keeper_path"]
LOG_FIELDS = ["path", "status", "http", "activity_id", "message", "at"]


@dataclass
class Candidate:
    path: Path
    start: datetime            # UTC, tz-aware
    duration_s: float
    sport: str
    device: str
    has_power: bool
    workout_id: str = ""
    file_id: str = ""
    title: str = ""
    sha256: str = ""
    action: str = "upload"
    reason: str = ""
    keeper: "Candidate | None" = field(default=None, repr=False)

    @property
    def end(self) -> datetime:
        return self.start + min(timedelta(seconds=self.duration_s), timedelta(hours=24))

    @property
    def family(self) -> str:
        return sport_family(self.sport)

    @property
    def is_ride(self) -> bool:
        return self.family == "ride"


# ---------------------------------------------------------------- pure helpers

def device_of(name: str) -> str:
    if "ELEMNT" in name or "Wahoo" in name:
        return "wahoo"
    if "TPAppleHealth" in name:
        return "applewatch"
    if re.search(r"garmin|edge|fenix|forerunner", name, re.I):
        return "garmin"
    return "other"


def sport_family(sport: str) -> str:
    s = sport.lower().replace(" ", "")
    return "ride" if s in RIDE_SPORTS else "run" if s in RUN_SPORTS else "other"


def compatible(fa: str, fb: str) -> bool:
    """Two recordings can be one session only if they're the same kind of activity.
    'other' (generic, training, tennis, walking...) pairs only with 'other'."""
    return fa == fb


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Same session when the overlap covers ≥ 50% of the shorter recording. Deliberately no
    start-time tolerance: a ride split into two sequential files (stop/restart on the ROAM)
    must keep both parts."""
    inter = (min(a_end, b_end) - max(a_start, b_start)).total_seconds()
    shorter = min((a_end - a_start).total_seconds(), (b_end - b_start).total_seconds())
    return inter > 0 and shorter > 0 and inter / shorter >= OVERLAP_FRACTION


def keeper_order(c: Candidate) -> tuple:
    return (not c.has_power, DEVICE_RANK.get(c.device, 9), -c.duration_s, str(c.path))


MAX_SESSION = timedelta(hours=24)


def icu_span(a: dict) -> tuple[datetime, datetime]:
    """(start, end) of an intervals.icu activity. Moving time first: elapsed time can be bogus
    (a device with an unset clock produced one 2015 ride 'lasting' months), and nothing counts
    as longer than 24 h, so one broken record can't swallow neighbouring sessions."""
    s = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00"))
    dur = timedelta(seconds=a.get("moving_time") or a.get("elapsed_time") or 60)
    return s, s + min(dur, MAX_SESSION)


def same_session(a: Candidate, b: Candidate) -> bool:
    return compatible(a.family, b.family) and overlaps(a.start, a.end, b.start, b.end)


def mark_duplicates(cands: list[Candidate]) -> None:
    """Best recording wins; anything recording the same session as an already-kept one is a duplicate.
    Comparing against keepers (not chaining through duplicates) means two separate sessions that
    happen to share an overlapping third recording are both kept."""
    keepers: list[Candidate] = []
    for c in sorted(cands, key=keeper_order):
        k = next((k for k in keepers if same_session(c, k)), None)
        if k is None:
            keepers.append(c)
        else:
            c.action, c.reason, c.keeper = "skip", "duplicate recording of the same session", k


def plan(cands: list[Candidate], existing: list[tuple[datetime, datetime, str]],
         uploaded_paths: set[str], defer_rides_from: date | None) -> list[Candidate]:
    """Mark each candidate upload / skip / defer, with the reason. Mutates and returns cands.
    existing: (start, end, sport family) of activities already in intervals.icu."""
    mark_duplicates(cands)
    for c in cands:
        if c.action == "skip":
            continue
        if str(c.path) in uploaded_paths:
            c.action, c.reason = "skip", "already uploaded (log)"
        elif any(compatible(c.family, fam) and overlaps(c.start, c.end, s, e) for s, e, fam in existing):
            c.action, c.reason = "skip", "already in intervals.icu"
        elif defer_rides_from and c.is_ride and c.start.date() >= defer_rides_from:
            c.action, c.reason = "defer", "ride in the Wahoo-sync era: wait for the backfill"
    return cands


# ---------------------------------------------------------------- file readers

def _payload(path: Path) -> bytes:
    raw = path.read_bytes()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def read_fit(data: bytes) -> tuple[datetime, float, str, bool]:
    import fitparse  # deferred: the planner logic is testable without fitparse

    fit = fitparse.FitFile(io.BytesIO(data))
    sess = next((m.get_values() for m in fit.get_messages("session")), {})
    start = sess.get("start_time")
    dur = sess.get("total_elapsed_time") or sess.get("total_timer_time") or 0.0
    sport = str(sess.get("sport") or "unknown")
    has_power = False
    first_ts = last_ts = None
    for m in fit.get_messages("record"):
        v = m.get_values()
        ts = v.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if not has_power and (v.get("power") or 0) > 0:
            has_power = True
    if start is None:
        start = first_ts
    if not dur and first_ts and last_ts:
        dur = (last_ts - first_ts).total_seconds()
    if start is None:
        raise ValueError("no start time in FIT")
    return start.replace(tzinfo=timezone.utc), float(dur), sport, has_power


def read_tcx(data: bytes) -> tuple[datetime, float, str, bool]:
    """Start, duration, sport and power presence from a TCX, by pattern rather than an XML parser:
    the files came back from a third-party server, and only four values are needed."""
    text = data.decode("utf-8", errors="replace")
    starts = re.findall(r'<Lap\b[^>]*StartTime="([^"]+)"', text)
    if not starts:
        raise ValueError("no laps in TCX")
    sport = re.search(r'<Activity\b[^>]*Sport="([^"]+)"', text)
    dur = sum(float(x) for x in re.findall(r"<TotalTimeSeconds>([0-9.]+)</TotalTimeSeconds>", text))
    start = datetime.fromisoformat(starts[0].replace("Z", "+00:00"))
    has_power = re.search(r"<(?:\w+:)?Watts>\s*[1-9]", text) is not None
    return start.astimezone(timezone.utc), dur, sport.group(1) if sport else "unknown", has_power


def read_gpx(data: bytes) -> tuple[datetime, float, str, bool]:
    """Start and duration from the first and last track-point times (by pattern, as for TCX)."""
    times = re.findall(r"<time>([^<]+)</time>", data.decode("utf-8", errors="replace"))
    if len(times) < 2:
        raise ValueError("no track-point times in GPX")
    first, last = (datetime.fromisoformat(t.strip().replace("Z", "+00:00")) for t in (times[0], times[-1]))
    return first.astimezone(timezone.utc), (last - first).total_seconds(), "gpx", False


def read_candidate(path: Path, row: dict | None, cards: dict[str, dict]) -> Candidate:
    data = _payload(path)
    name = path.name.lower()
    reader = read_tcx if ".tcx" in name else read_gpx if ".gpx" in name else read_fit
    start, dur, sport, power = reader(data)
    wid = (row or {}).get("workout_id", "")
    return Candidate(path=path, start=start, duration_s=dur, sport=sport, device=device_of(path.name),
                     has_power=power, workout_id=wid, file_id=(row or {}).get("file_id", ""),
                     title=(cards.get(wid) or {}).get("title") or "", sha256=hashlib.sha256(data).hexdigest())


def load_cards(archive: Path) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for p in sorted((archive / "workouts").glob("*.json")):
        for c in json.loads(p.read_text(encoding="utf-8")):
            cards[str(c.get("workoutId"))] = c
    return cards


def load_log(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {r["path"] for r in csv.DictReader(f) if r["status"] == "uploaded"}


def append_log(path: Path, row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------- main

def parse_date_or_none(text: str) -> date | None:
    return None if text.lower() == "none" else date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Upload archived TP history to intervals.icu, without duplicates.")
    p.add_argument("--root", type=Path, default=REPO_ROOT)
    p.add_argument("--since", type=date.fromisoformat, default=date(2010, 1, 1))
    p.add_argument("--until", type=date.fromisoformat, default=date.today())
    p.add_argument("--defer-rides-from", type=parse_date_or_none, default=date(2026, 1, 1),
                   help="hold back rides from this date (Wahoo sync era); 'none' to upload them")
    p.add_argument("--apply", action="store_true", help="upload; without it, only print and write the plan")
    p.add_argument("--max-uploads", type=int, default=None, help="stop after N uploads (for a first test)")
    args = p.parse_args(argv)

    from icu_client import ICUClient

    root = args.root
    export = root / "rides" / "fit" / "tp-export"
    cards = load_cards(root / "notes" / "tp-archive")
    with (export / "manifest.csv").open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("kind") or "device") == "device"]

    cands: list[Candidate] = []
    unreadable: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if not (args.since.isoformat() <= r["workout_day"] <= args.until.isoformat()):
            continue
        try:
            c = read_candidate(root / r["path"], r, cards)
        except Exception as e:  # a corrupt file must not stop the plan; it's reported below
            unreadable.append(f"{r['path']}: {type(e).__name__} {e}")
            continue
        seen.add(c.sha256)
        cands.append(c)
    for fit in sorted((root / "rides" / "fit").glob("*.fit")):
        try:
            c = read_candidate(fit, None, cards)
        except Exception as e:
            unreadable.append(f"{fit}: {type(e).__name__} {e}")
            continue
        if c.sha256 not in seen and args.since <= c.start.date() <= args.until:
            cands.append(c)

    icu = ICUClient()
    acts = icu.activities(f"{args.since}", f"{args.until + timedelta(days=1)}",
                          fields="id,start_date,elapsed_time,moving_time,source,type")
    existing = []
    for a in acts:
        existing.append((*icu_span(a), sport_family(a.get("type") or "")))

    log_path = export / "icu-upload-log.csv"
    plan(cands, existing, load_log(log_path), args.defer_rides_from)

    plan_rows = [{
        "action": c.action, "reason": c.reason, "start_utc": c.start.isoformat(), "duration_s": round(c.duration_s),
        "sport": c.sport, "device": c.device, "has_power": c.has_power, "workout_id": c.workout_id,
        "file_id": c.file_id, "title": c.title, "path": str(c.path.relative_to(root)) if c.path.is_relative_to(root) else str(c.path),
        "keeper_path": str(c.keeper.path.name) if c.keeper else ""} for c in sorted(cands, key=lambda c: c.start)]
    with (export / "icu-upload-plan.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_FIELDS)
        w.writeheader()
        w.writerows(plan_rows)

    summary: dict[tuple[str, str], int] = {}
    for c in cands:
        summary[(c.action, c.reason)] = summary.get((c.action, c.reason), 0) + 1
    print(f"intervals.icu already has {len(existing)} activities in range; {len(cands)} candidate files")
    for (action, reason), n in sorted(summary.items()):
        print(f"  {action:7s} {n:5d}  {reason}")
    for u in unreadable:
        print(f"  UNREADABLE {u}", file=sys.stderr)
    print(f"plan written to {export / 'icu-upload-plan.csv'}")
    if not args.apply:
        return 1 if unreadable else 0

    done = 0
    failures = 0
    for c in sorted((c for c in cands if c.action == "upload"), key=lambda c: c.start):
        if args.max_uploads is not None and done >= args.max_uploads:
            break
        ext = f"tp:{c.workout_id}:{c.file_id}" if c.workout_id else f"local:{c.sha256[:16]}"
        r = icu.upload_activity(c.path, name=c.title or None, external_id=ext)
        try:
            body = r.json()
        except ValueError:
            body = {}
        ok = r.status_code in (200, 201)
        failures += not ok
        append_log(log_path, {"path": str(c.path), "status": "uploaded" if ok else "failed", "http": r.status_code,
                              "activity_id": body.get("id", "") if isinstance(body, dict) else "",
                              "message": "" if ok else r.text[:200], "at": datetime.now(timezone.utc).isoformat()})
        done += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {c.start:%Y-%m-%d %H:%M} {c.sport:10s} {c.device:10s} {c.path.name[:60]}")
    print(f"uploaded {done - failures}, failed {failures}")
    return 1 if failures or unreadable else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
