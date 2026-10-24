"""Find (and, with --apply, delete) duplicate activities in intervals.icu.

Two activities are the same session when they are the same kind of activity and
overlap by >= 50% of the shorter one (the rule icu_upload_history.py uses). In each
group the keeper is ours, so the TP-aligned load and its link to the TP card
survive: an activity with a "tp:" / "tp-manual:" / "tp-strength:" external_id,
then one with power, then the longest. Typical source: intervals.icu's Wahoo
sync re-delivering a ride the history import already uploaded.

Dry run by default (prints the groups). --apply deletes the non-keepers; deleting
is irreversible, so read the dry run first.

Usage:
    python scripts/icu_duplicates.py                  # report, since 2015
    python scripts/icu_duplicates.py --since 2026-01-01 --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from icu_upload_history import compatible, icu_span, overlaps, sport_family  # noqa: E402

FIELDS = "id,start_date,elapsed_time,moving_time,type,source,external_id,icu_training_load,device_watts,name"


span = icu_span


def keeper_rank(a: dict) -> tuple:
    ours = (a.get("external_id") or "").startswith(("tp:", "tp-manual:", "tp-strength:"))
    s, e = span(a)
    return (not ours, not a.get("device_watts"), -(e - s).total_seconds(), str(a["id"]))


def duplicate_groups(acts: list[dict]) -> list[list[dict]]:
    """[keeper, dup, dup...] per group; each activity is compared with kept ones only (no chaining)."""
    groups: list[list[dict]] = []
    for a in sorted(acts, key=keeper_rank):
        s, e = span(a)
        fam = sport_family(a.get("type") or "")
        for g in groups:
            ks, ke = span(g[0])
            if compatible(fam, sport_family(g[0].get("type") or "")) and overlaps(s, e, ks, ke):
                g.append(a)
                break
        else:
            groups.append([a])
    return [g for g in groups if len(g) > 1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Find and remove duplicate intervals.icu activities.")
    p.add_argument("--since", type=date.fromisoformat, default=date(2015, 1, 1))
    p.add_argument("--until", type=date.fromisoformat, default=date.today())
    p.add_argument("--apply", action="store_true", help="delete the duplicates (irreversible)")
    args = p.parse_args(argv)

    from icu_client import ICUClient

    icu = ICUClient()
    acts = icu.activities(str(args.since), str(args.until + timedelta(days=1)), fields=FIELDS)
    groups = duplicate_groups(acts)
    print(f"{len(acts)} activities, {len(groups)} duplicate groups, {sum(len(g) - 1 for g in groups)} to remove")
    for g in groups:
        for i, a in enumerate(g):
            tag = "KEEP  " if i == 0 else "delete"
            print(f"  {tag} {a['id']:>12} {a['start_date'][:16]} {a.get('type', ''):14s} {a.get('source', ''):7s} "
                  f"load {a.get('icu_training_load')} {a.get('external_id') or ''}")
    if args.apply:
        for g in groups:
            for a in g[1:]:
                r = icu._req("DELETE", f"/activity/{a['id']}", athlete_scoped=False)
                r.raise_for_status()
        print(f"deleted {sum(len(g) - 1 for g in groups)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
