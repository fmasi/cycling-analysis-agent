"""Clean up after a ROUVY ride: one copy per app, the one with power.

ROUVY uploads its own FIT to intervals.icu (power, cadence, HR, paired to the plan) and also writes
the ride to Apple Health, which HealthFit then re-exports to intervals.icu and TrainingPeaks as an
HR-only "Indoor Cycling" copy. The Apple Health write stays (Watch credit), so the copies are
removed downstream:

    intervals.icu   delete the HealthFit copy; keep the ROUVY activity
    TrainingPeaks   make sure a card carries ROUVY's FIT (create one and upload the file from
                    intervals.icu), then delete the HealthFit card

TP is handled with a fresh card on purpose: uploading the ROUVY FIT onto the HealthFit card merges
the two files (time and distance doubled, average power halved) and removing a file afterwards
does not recompute the totals. TP also refuses (422) a FIT that already sits on another card.

Dry run by default; --apply makes the changes. Deletions are permanent. Safe to re-run: a day
that is already clean reports nothing to do.

Usage:
    python scripts/rouvy_cleanup.py                       # today
    python scripts/rouvy_cleanup.py --date 2026-10-26 --apply
    python scripts/rouvy_cleanup.py --since 2026-10-20 --skip-tp
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# HealthFit names its exports "<YYYY-MM-DD>-<HHMMSS>-<workout type>-<source app>.fit"; TP keeps the
# name (spaces become dashes, plus .gz).
HEALTHFIT_ROUVY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-.+-ROUVY\.fit(\.gz)?$", re.I)
SAME_SESSION_S = 300        # starts this close are the same ride
SAME_DURATION_S = 180       # TP card duration vs the ROUVY activity's timer time
ICU_FIELDS = ("id,start_date_local,type,name,device_name,external_id,moving_time,elapsed_time,"
              "icu_training_load,icu_average_watts,icu_weighted_avg_watts,paired_event_id")


def is_rouvy(a: dict) -> bool:
    """ROUVY's own upload: 'VIRTUALTRAINING Rouvy' device, 'ROUVY - …' name."""
    return "rouvy" in (a.get("device_name") or "").lower() or (a.get("name") or "").upper().startswith("ROUVY")


def is_healthfit_copy(a: dict) -> bool:
    """The Apple Health copy HealthFit re-exported: ROUVY-named file, no power."""
    return (not is_rouvy(a) and bool(HEALTHFIT_ROUVY_FILE.match(a.get("external_id") or ""))
            and not a.get("icu_average_watts"))


def start(a: dict) -> datetime:
    return datetime.fromisoformat(a["start_date_local"])


def icu_copies(acts: list[dict]) -> list[tuple[dict, dict]]:
    """(rouvy, healthfit_copy) pairs on the same ride."""
    rides = [a for a in acts if is_rouvy(a)]
    pairs = []
    for c in (a for a in acts if is_healthfit_copy(a)):
        match = next((r for r in rides if abs((start(r) - start(c)).total_seconds()) <= SAME_SESSION_S), None)
        if match:
            pairs.append((match, c))
    return pairs


def card_title(rouvy: dict) -> str:
    """'ROUVY - Tempo endurance 3x8 (Rouvy test) - 2026-10-26' -> 'Tempo endurance 3x8 (Rouvy test)'."""
    name = re.sub(r"^ROUVY\s*-\s*", "", rouvy.get("name") or "", flags=re.I)
    name = re.sub(r"\s*-\s*\d{4}-\d{2}-\d{2}$", "", name)
    return name or "ROUVY ride"


def upload_name(rouvy: dict) -> str:
    return f"{rouvy['start_date_local'][:10]}-rouvy-{rouvy['id']}.fit"


def tp_card_has_rouvy(card: dict, rouvy: dict) -> bool:
    """A TP card already carries this ROUVY ride: our upload name, or power + a matching duration."""
    names = [f.get("file_name") or "" for f in card.get("device_files") or []]
    if any(n.startswith(upload_name(rouvy).removesuffix(".fit")) for n in names):
        return True
    m = card.get("metrics") or {}
    hours = m.get("duration_actual")
    if not m.get("normalized_power") or hours is None:
        return False
    timer = rouvy.get("moving_time") or rouvy.get("elapsed_time") or 0
    return abs(hours * 3600 - timer) <= SAME_DURATION_S


def tp_is_healthfit_copy(card: dict) -> bool:
    names = [f.get("file_name") or "" for f in card.get("device_files") or []]
    m = card.get("metrics") or {}
    return bool(names) and all(HEALTHFIT_ROUVY_FILE.match(n) for n in names) and not m.get("avg_power")


def days(args: argparse.Namespace) -> list[str]:
    if args.since:
        d0 = date.fromisoformat(args.since)
        return [(d0 + timedelta(i)).isoformat() for i in range((date.today() - d0).days + 1)]
    return [args.date or date.today().isoformat()]


def clean_icu(icu, day: str, apply: bool) -> list[dict]:
    acts = icu.activities(day, day, fields=ICU_FIELDS)
    rides = [a for a in acts if is_rouvy(a)]
    if rides:
        print(f"{day}: {len(rides)} ROUVY ride(s)")
    for rouvy, copy in icu_copies(acts):
        print(f"  icu  keep {rouvy['id']} {rouvy.get('name')!r} (load {rouvy.get('icu_training_load')}, "
              f"{rouvy.get('icu_average_watts')} W)")
        print(f"  icu  {'DELETE' if apply else 'would delete'} {copy['id']} {copy.get('name')!r} "
              f"({copy.get('device_name')}, load {copy.get('icu_training_load')}, no power)")
        if apply:
            r = icu._req("DELETE", f"/activity/{copy['id']}", athlete_scoped=False)
            r.raise_for_status()
    return rides


async def clean_tp(icu, day: str, rides: list[dict], apply: bool) -> int:
    from tp_mcp.tools.workout_files import tp_upload_workout_file
    from tp_mcp.tools.workouts import tp_create_workout, tp_delete_workout, tp_get_workout, tp_get_workouts

    listed = await tp_get_workouts(day, day, workout_filter="completed")
    if listed.get("isError"):
        raise SystemExit(f"TP: {listed.get('message')}")
    cards = []
    for w in listed.get("workouts", []):
        if w.get("sport") == "Bike":
            full = await tp_get_workout(str(w["id"]))
            if not full.get("isError"):
                cards.append(full)
    problems = 0
    for rouvy in rides:
        if any(tp_card_has_rouvy(c, rouvy) for c in cards):
            print(f"  tp   ROUVY file already on a card for {rouvy['id']}")
        else:
            title = card_title(rouvy)
            print(f"  tp   {'CREATE' if apply else 'would create'} card {title!r} + upload {upload_name(rouvy)}")
            if apply:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / upload_name(rouvy)
                    r = icu._req("GET", f"/activity/{rouvy['id']}/fit-file", athlete_scoped=False)
                    r.raise_for_status()
                    path.write_bytes(r.content)
                    made = await tp_create_workout(day, "Bike", title,
                                                   duration_minutes=max(1, round((rouvy.get("moving_time") or 60) / 60)),
                                                   description=f"ROUVY ride (intervals.icu {rouvy['id']}); "
                                                               "HealthFit HR-only copy removed.")
                    if made.get("isError"):
                        print(f"  tp   create failed: {made.get('message')}")
                        problems += 1
                        continue
                    up = await tp_upload_workout_file(str(made["workout_id"]), file_path=str(path), workout_day=day)
                    if up.get("isError"):
                        print(f"  tp   upload failed ({up.get('message')}); card {made['workout_id']} left empty")
                        problems += 1
                        continue
        for c in cards:
            if tp_is_healthfit_copy(c):
                print(f"  tp   {'DELETE' if apply else 'would delete'} HealthFit card {c['id']} {c.get('title')!r} "
                      f"(TSS {(c.get('metrics') or {}).get('tss_actual')}, no power)")
                if apply:
                    d = await tp_delete_workout(str(c["id"]))
                    if d.get("isError"):
                        print(f"  tp   delete failed: {d.get('message')}")
                        problems += 1
        cards = [c for c in cards if not tp_is_healthfit_copy(c)]
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Remove HealthFit copies of ROUVY rides in intervals.icu and TP.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", help="YYYY-MM-DD (default today)")
    g.add_argument("--since", help="YYYY-MM-DD, every day up to today")
    p.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    p.add_argument("--skip-tp", action="store_true", help="intervals.icu only (after TP is dropped)")
    args = p.parse_args(argv)

    from icu_client import ICUClient
    icu = ICUClient()
    problems = 0
    for day in days(args):
        rides = clean_icu(icu, day, args.apply)
        if rides and not args.skip_tp:
            problems += asyncio.run(clean_tp(icu, day, rides, args.apply))
    if not args.apply:
        print("dry run: nothing changed (--apply to make the changes)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
