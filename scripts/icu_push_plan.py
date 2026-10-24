"""Put a block of planned sessions on the intervals.icu calendar (and from there the ROAM / Watch).

Input is a JSON list, one entry per session:
    {"date": "2026-10-31", "type": "Ride", "name": "Long Z2 1h30 (HR)", "load": 63, "minutes": 90,
     "target": "HR", "indoor": false, "category": "WORKOUT", "description": "...text-workout..."}
`category` defaults to WORKOUT ("NOTE" for rest days); `target` (POWER / HR / PACE) tells
intervals.icu which target the structured steps use. Steps written in intervals.icu's text-workout
syntax inside `description` become the structured workout that is pushed to Wahoo.

Every entry gets external_id "plan:<date>:<slug of name>", and the bulk endpoint upserts on it, so
re-running after editing the file updates the sessions instead of duplicating them. Dry run by
default; --apply writes, then reads the events back and reports parse/push problems.

Usage:
    python scripts/icu_push_plan.py plans/2026-W44-W45-icu.json
    python scripts/icu_push_plan.py plans/2026-W44-W45-icu.json --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SPORTS_WITH_STEPS = {"Ride", "VirtualRide", "Run", "VirtualRun", "Swim"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def to_event(s: dict) -> dict:
    """One plan entry -> an intervals.icu event payload."""
    category = s.get("category", "WORKOUT")
    ev = {
        "category": category,
        "start_date_local": f"{s['date']}T00:00:00",
        "name": s["name"],
        "description": s.get("description", ""),
        "external_id": f"plan:{s['date']}:{slug(s['name'])}",
    }
    if category == "WORKOUT":
        ev["type"] = s["type"]
        if s.get("load") is not None:
            ev["icu_training_load"] = int(s["load"])
        if s.get("minutes"):
            ev["moving_time"] = int(s["minutes"] * 60)
        if s.get("target"):
            ev["target"] = s["target"]
        if s.get("indoor"):
            ev["indoor"] = True
    return ev


def check(ev: dict, back: dict) -> list[str]:
    """Problems with a stored event: structure expected but not parsed, or push errors."""
    problems = []
    has_steps = bool(re.search(r"^\s*-\s+\d", ev.get("description", ""), re.M))
    if ev.get("type") in SPORTS_WITH_STEPS and has_steps and not (back.get("workout_doc") or {}).get("steps"):
        problems.append("structured steps in the description were not parsed")
    if back.get("push_errors"):
        problems.append(f"push errors: {back['push_errors']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Put planned sessions on the intervals.icu calendar.")
    p.add_argument("plan", type=Path)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    sessions = json.loads(args.plan.read_text(encoding="utf-8"))
    events = [to_event(s) for s in sessions]
    for e in events:
        print(f"  {e['start_date_local'][:10]} {e.get('type', e['category']):14s} "
              f"{e.get('icu_training_load', ''):>4} {e['name']}")
    print(f"{len(events)} events, planned load {sum(e.get('icu_training_load', 0) for e in events)}")
    if not args.apply:
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from icu_client import ICUClient

    icu = ICUClient()
    r = icu._req("POST", "/events/bulk", json=events, params={"upsert": "true"})
    r.raise_for_status()
    stored = {e.get("external_id"): e for e in r.json()}
    problems = 0
    for ev in events:
        back = stored.get(ev["external_id"])
        if back is None:
            print(f"  MISSING {ev['external_id']}")
            problems += 1
            continue
        for msg in check(ev, back):
            print(f"  {ev['start_date_local'][:10]} {ev['name']}: {msg}")
            problems += 1
    print(f"stored {len(stored)} events, {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
