"""Make intervals.icu's historical training load match TrainingPeaks' TSS.

intervals.icu scores an uploaded file against today's FTP/LTHR, so years-old
sessions come out far too low or high (2015 rides at FTP ~100 W read at a third
of their TP TSS against today's 191 W). For history up to --until (the start of
the parallel run), this sets each activity's load to what TP recorded:

- an activity uploaded by icu_upload_history.py (external_id "tp:<workoutId>:<fileId>")
  gets its card's TSS plus the TSS of any duplicate recordings the uploader
  dropped in its favour (from icu-upload-plan.csv), so each day's total matches TP;
- any other activity (e.g. Wahoo-synced) is matched to a TP file by overlapping
  start time and kind of activity, then gets that card's TSS the same way;
- a TP card with TSS but no file at all becomes a manual activity
  (external_id "tp-manual:<workoutId>", upserted, so re-runs don't duplicate);
- a TP strength-builder workout (archived under notes/tp-archive/strength/; they
  never appear as cards) becomes a manual WeightTraining activity
  ("tp-strength:<id>"), unless intervals.icu already has a strength-type
  activity overlapping it (e.g. the Watch session arrived via HealthFit), in
  which case that activity gets the TSS instead.

intervals.icu keeps its own power_load/hr_load alongside the override, for comparison.
Dry run by default; --apply writes. Then compares intervals.icu's daily load and
CTL/ATL against the TP archive's PMC and reports the differences.

Usage:
    python scripts/icu_tp_loads.py                 # plan + current comparison
    python scripts/icu_tp_loads.py --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from icu_upload_history import compatible, icu_span, overlaps, sport_family  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TP_TYPE_TO_ICU = {1: "Swim", 2: "Ride", 3: "Run", 4: "Workout", 5: "Workout", 6: "Workout", 8: "MountainBikeRide",
                  9: "WeightTraining", 10: "Workout", 11: "NordicSki", 12: "Rowing", 13: "Walk", 100: "Workout"}


def load_cards(archive: Path) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for p in sorted((archive / "workouts").glob("*.json")):
        for c in json.loads(p.read_text(encoding="utf-8")):
            cards[str(c["workoutId"])] = c
    return cards


def tss(card: dict | None) -> float:
    return float((card or {}).get("tssActual") or 0.0)


def group_tss(plan_rows: list[dict], cards: dict[str, dict]) -> dict[str, float]:
    """keeper file name -> TSS of every distinct TP card in its duplicate group (keeper included)."""
    members: dict[str, set[str]] = defaultdict(set)
    for r in plan_rows:
        name = Path(r["path"]).name
        keeper = r["keeper_path"] or name
        if r["workout_id"]:
            members[keeper].add(r["workout_id"])
    return {k: sum(tss(cards.get(w)) for w in wids) for k, wids in members.items()}


def target_loads(acts: list[dict], plan_rows: list[dict], cards: dict[str, dict]) -> dict[str, tuple[int, str]]:
    """icu activity id -> (target load, how it was matched). Activities with no TP match are absent."""
    by_name = {Path(r["path"]).name: r for r in plan_rows}
    gtss = group_tss(plan_rows, cards)
    spans = []
    for r in plan_rows:
        if r["action"] == "skip" and r["keeper_path"]:
            continue                       # a dropped duplicate: its TSS travels with its keeper
        s = datetime.fromisoformat(r["start_utc"])
        spans.append((s, s + timedelta(seconds=float(r["duration_s"])), sport_family(r["sport"]), Path(r["path"]).name))
    out: dict[str, tuple[int, str]] = {}
    for a in acts:
        ext = a.get("external_id") or ""
        name = None
        if ext.startswith("tp:"):
            _, wid, fid = (ext.split(":") + ["", ""])[:3]
            name = next((n for n, r in by_name.items() if r["workout_id"] == wid and r["file_id"] == fid), None)
            how = "external_id"
        if name is None and a.get("start_date"):
            s, e = icu_span(a)
            fam = sport_family(a.get("type") or "")
            name = next((n for ss, ee, f, n in spans if compatible(fam, f) and overlaps(s, e, ss, ee)), None)
            how = "start-time match"
        if name is not None and name in gtss:
            out[a["id"]] = (round(gtss[name]), how)
    return out


def manual_entries(cards: dict[str, dict], plan_rows: list[dict], since: date, until: date) -> list[dict]:
    """Manual intervals.icu activities for TP cards that carry TSS but have no file anywhere in the plan."""
    with_file = {r["workout_id"] for r in plan_rows if r["workout_id"]}
    out = []
    for wid, c in cards.items():
        day = (c.get("workoutDay") or "")[:10]
        if wid in with_file or tss(c) <= 0 or not (since.isoformat() <= day <= until.isoformat()):
            continue
        start = (c.get("startTime") or f"{day}T12:00:00")[:19]
        out.append({
            "external_id": f"tp-manual:{wid}",
            "start_date_local": start,
            "type": TP_TYPE_TO_ICU.get(c.get("workoutTypeValueId"), "Workout"),
            "name": c.get("title") or "TP session",
            # intervals.icu rejects a zero-duration manual activity; a TP card can carry TSS without time.
            "moving_time": max(int((c.get("totalTime") or 0) * 3600), 60),
            "icu_training_load": round(tss(c)),
            "description": "Imported from TrainingPeaks (no file); load = TP TSS."
                           + ("" if c.get("totalTime") else " TP recorded no duration (1 min placeholder)."),
        })
    return out


def strength_entries(archive: Path, since: date, until: date) -> list[dict]:
    """Manual WeightTraining activities for TP strength-builder workouts with a completed TSS."""
    out = []
    for p in sorted((archive / "strength").glob("*/*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        d = e.get("detail") or {}
        lst = e.get("listing") or {}
        load = d.get("completedTss") or lst.get("completedTss")
        start = (d.get("startDateTime") or lst.get("startDateTime") or d.get("completedDateTime")
                 or (lst.get("prescribedDate") or "")[:10] + "T12:00:00")[:19]
        if not load or not (since.isoformat() <= start[:10] <= until.isoformat()):
            continue
        out.append({
            "external_id": f"tp-strength:{d.get('id') or lst.get('id')}",
            "start_date_local": start,
            "type": "WeightTraining",
            "name": d.get("title") or lst.get("title") or "Strength",
            "moving_time": max(int(d.get("executedDurationInSeconds") or 0), 60),
            "icu_training_load": round(float(load)),
            "description": f"Imported from TrainingPeaks strength builder; load = TP TSS ({d.get('completedTssSource')}).",
        })
    return out


def split_strength(entries: list[dict], acts: list[dict]) -> tuple[list[dict], list[tuple[dict, int]]]:
    """(entries to create, [(existing icu activity, load)]) — an existing WeightTraining activity
    that overlaps a TP strength session takes its TSS instead of a second, manual copy."""
    create, assign = [], []
    for e in entries:
        s = datetime.fromisoformat(e["start_date_local"])
        en = s + timedelta(seconds=max(e["moving_time"], 60))
        hit = None
        for a in acts:
            if a.get("type") != "WeightTraining" or (a.get("external_id") or "").startswith("tp-strength:"):
                continue
            a_s = datetime.fromisoformat(a["start_date_local"][:19])
            a_e = a_s + timedelta(seconds=a.get("elapsed_time") or a.get("moving_time") or 60)
            if overlaps(s, en, a_s, a_e):
                hit = a
                break
        if hit is None:
            create.append(e)
        else:
            assign.append((hit, e["icu_training_load"]))
    return create, assign


def compare_pmc(icu, archive: Path, since: date, until: date) -> None:
    with (archive / "fitness" / "pmc-daily.csv").open(encoding="utf-8") as f:
        tp = {r["date"]: r for r in csv.DictReader(f) if since.isoformat() <= r["date"] <= until.isoformat()}
    well = {w["id"]: w for w in icu.get("/wellness", oldest=str(since), newest=str(until))}
    acts = icu.activities(str(since), str(until + timedelta(days=1)), fields="id,start_date_local,icu_training_load")
    daily: dict[str, float] = defaultdict(float)
    for a in acts:
        daily[a["start_date_local"][:10]] += a.get("icu_training_load") or 0
    load_diffs = [(d, float(r["tss_actual"]), daily.get(d, 0.0)) for d, r in tp.items()
                  if abs(float(r["tss_actual"]) - daily.get(d, 0.0)) > 1.5]
    ctl_d = [abs(float(r["ctl"]) - (well.get(d, {}).get("ctl") or 0)) for d, r in tp.items() if d in well]
    atl_d = [abs(float(r["atl"]) - (well.get(d, {}).get("atl") or 0)) for d, r in tp.items() if d in well]
    print(f"PMC check {since}..{until}: {len(load_diffs)} days where daily load differs by > 1.5 TSS")
    for d, t, i in sorted(load_diffs)[:15]:
        print(f"   {d}  TP {t:7.1f}  icu {i:7.1f}")
    if ctl_d:
        print(f"   CTL |TP - icu| max {max(ctl_d):.2f}, mean {sum(ctl_d) / len(ctl_d):.2f};"
              f" ATL max {max(atl_d):.2f}, mean {sum(atl_d) / len(atl_d):.2f}  ({len(ctl_d)} days)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set intervals.icu historical load to TrainingPeaks' TSS.")
    p.add_argument("--root", type=Path, default=REPO_ROOT)
    p.add_argument("--since", type=date.fromisoformat, default=date(2015, 1, 1))
    p.add_argument("--until", type=date.fromisoformat, default=date(2026, 10, 25),
                   help="last day of history to align (default 2026-10-25, the eve of the parallel run)")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    from icu_client import ICUClient

    archive = args.root / "notes" / "tp-archive"
    cards = load_cards(archive)
    with (args.root / "rides" / "fit" / "tp-export" / "icu-upload-plan.csv").open(newline="", encoding="utf-8") as f:
        plan_rows = list(csv.DictReader(f))
    icu = ICUClient()
    acts = icu.activities(str(args.since), str(args.until + timedelta(days=1)),
                          fields="id,start_date,start_date_local,elapsed_time,moving_time,type,external_id,"
                                 "icu_training_load,source")
    acts = [a for a in acts if (a.get("start_date_local") or "")[:10] <= args.until.isoformat()]
    targets = target_loads(acts, plan_rows, cards)
    strength_new, strength_assign = split_strength(strength_entries(archive, args.since, args.until),
                                                   [a for a in acts if a["id"] not in targets])
    for a, load in strength_assign:
        targets[a["id"]] = (load, "strength match")
    changes = [(a, *targets[a["id"]]) for a in acts if a["id"] in targets
               and (a.get("icu_training_load") or 0) != targets[a["id"]][0]]
    unmatched = [a for a in acts if a["id"] not in targets
                 and not (a.get("external_id") or "").startswith(("tp-manual:", "tp-strength:"))]
    manual = manual_entries(cards, plan_rows, args.since, args.until) + strength_new
    # Don't rely on the bulk endpoint's upsert (documented for OAuth apps): skip ids already there.
    present = {a.get("external_id") for a in acts if a.get("external_id")}
    manual = [e for e in manual if e["external_id"] not in present]
    print(f"{len(acts)} intervals.icu activities in range: {len(changes)} loads to set, "
          f"{len(acts) - len(changes) - len(unmatched)} already right, {len(unmatched)} with no TP match")
    for a in unmatched[:10]:
        print(f"   no TP match: {a.get('start_date_local')} {a.get('type')} {a.get('source')} load {a.get('icu_training_load')}")
    n_strength = sum(e["external_id"].startswith("tp-strength:") for e in manual)
    print(f"manual activities to create: {len(manual) - n_strength} file-less TP cards, {n_strength} strength-builder "
          f"workouts (already present are skipped); {len(strength_assign)} strength sessions matched to existing activities")
    if args.apply:
        for a, load, _ in changes:
            icu.update_activity(a["id"], {"icu_training_load": load})
        if manual:
            icu.create_manual_activities(manual)
        print(f"applied {len(changes)} load overrides and {len(manual)} manual activities")
    compare_pmc(icu, archive, args.since, args.until)
    return 0


if __name__ == "__main__":
    sys.exit(main())
