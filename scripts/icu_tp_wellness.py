"""Copy the TrainingPeaks metrics history (body-comp/tp-metrics-history.csv, from tp_export.py)
into intervals.icu wellness, without overwriting anything intervals.icu already has.

Mapping (TP label -> intervals.icu field):
    Weight            -> weight     first Withings reading of the day (the fasted morning weigh-in);
                                    a Garmin-relayed value only on days with no Withings reading
    Sleep Hours       -> sleepSecs
    Pulse (Garmin)    -> restingHR  Withings "Pulse" is the blood-pressure reading's pulse: skipped
    HRV               -> hrv        Garmin's overnight value
    Blood Pressure    -> systolic / diastolic
    Percent Fat       -> bodyFat
    Steps             -> steps
    Injury, Stress Level, Body Battery, sleep stages, Sleep Quality, Overall Feeling, Notes
                      -> comments   as text, because intervals.icu's scales don't match TP's
                                    (TP injury is 1-10, 10 = healthy)

A field intervals.icu already holds for a day (e.g. from HealthFit) is left alone, and so are
comments already written. Dry run by default; --apply writes with wellness-bulk.

Usage:
    python scripts/icu_tp_wellness.py
    python scripts/icu_tp_wellness.py --apply
"""
from __future__ import annotations

import argparse
import ast
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMENT_LABELS = ("Injury", "Stress Level", "Body Battery", "Time in Light Sleep", "Time in Deep Sleep",
                  "Time in REM Sleep", "Time Awake", "Sleep Quality", "Overall Feeling", "Notes")


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def day_values(rows: list[dict]) -> dict[str, dict]:
    """TP metric rows -> {date: {intervals.icu field: value}}."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    out: dict[str, dict] = {}
    for day, items in by_day.items():
        f: dict = {}
        weights = sorted((r for r in items if r["label"] == "Weight" and _num(r["value"])),
                         key=lambda r: (r["source"] != "Withings", r["time"] or ""))
        if weights:
            f["weight"] = round(float(weights[0]["value"]), 2)
        comments = []
        for r in sorted(items, key=lambda r: r["time"] or ""):
            label, v, src = r["label"], r["value"], r["source"]
            if label == "Sleep Hours" and _num(v):
                f.setdefault("sleepSecs", int(round(float(v) * 3600)))
            elif label == "Pulse" and src != "Withings" and _num(v):
                f.setdefault("restingHR", int(round(float(v))))
            elif label == "HRV" and _num(v):
                f.setdefault("hrv", float(v))
            elif label == "Percent Fat" and _num(v):
                f.setdefault("bodyFat", round(float(v), 1))
            elif label == "Steps" and _num(v):
                f.setdefault("steps", int(float(v)))
            elif label == "Blood Pressure":
                try:
                    sys_, dia = ast.literal_eval(v)[:2]
                    f.setdefault("systolic", int(sys_))
                    f.setdefault("diastolic", int(dia))
                except (ValueError, SyntaxError, TypeError):
                    pass
            elif label in COMMENT_LABELS:
                if label == "Injury":
                    comments.append(f"TP injury {v}/10 (10 = healthy)")
                elif label in ("Stress Level", "Body Battery"):
                    try:
                        lo, hi, avg = ast.literal_eval(v)[:3]
                        comments.append(f"{label} avg {avg} (min {lo}, max {hi})")
                    except (ValueError, SyntaxError, TypeError):
                        comments.append(f"{label} {v}")
                elif label.startswith("Time "):
                    if _num(v):
                        comments.append(f"{label} {float(v):.1f} h")
                else:
                    comments.append(f"{label}: {v}")
        if comments:
            f["comments"] = "From TrainingPeaks: " + "; ".join(comments)
        if f:
            out[day] = f
    return out


def missing_only(new: dict[str, dict], existing: dict[str, dict]) -> list[dict]:
    """wellness-bulk records carrying only the fields intervals.icu doesn't have yet."""
    records = []
    for day, fields in sorted(new.items()):
        have = existing.get(day, {})
        keep = {k: v for k, v in fields.items() if have.get(k) in (None, "")}
        if keep:
            records.append({"id": day, **keep})
    return records


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Copy TP metrics history into intervals.icu wellness.")
    p.add_argument("--root", type=Path, default=REPO_ROOT)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from icu_client import ICUClient

    with (args.root / "body-comp" / "tp-metrics-history.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    new = day_values(rows)
    icu = ICUClient()
    first, last = min(new), max(new)
    existing = {w["id"]: w for w in icu.get("/wellness", oldest=first, newest=last)}
    records = missing_only(new, existing)
    counts: dict[str, int] = defaultdict(int)
    for r in records:
        for k in r:
            if k != "id":
                counts[k] += 1
    print(f"{len(new)} days with TP metrics ({first}..{last}); {len(records)} days to write: "
          + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    if args.apply:
        for i in range(0, len(records), 200):
            r = icu._req("PUT", "/wellness-bulk", json=records[i:i + 200])
            r.raise_for_status()
        print(f"wrote {len(records)} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
