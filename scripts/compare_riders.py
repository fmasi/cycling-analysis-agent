#!/usr/bin/env python
"""Compare two riders' FIT files from the same ride.

One-shot orchestrator: parse both FITs, sync on time, compute headline
metrics + power curves + per-climb + flat sections + flat attacks, and
render a side-by-side comparison markdown.

Usage:
    python scripts/compare_riders.py <rider_fit> <thomas_fit> \\
        [--rider-label "Frédéric"] [--peer-label "Thomas"] \\
        [--gpx <route_gpx>] [--out <output.md>]

The optional --gpx feeds the same route into verify_climbs so the climb
spans come from the canonical hi-fi verification rather than each FIT's
own find_climbs (which can disagree).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyse_fit import parse_fit, to_arrays


# --------------------------------------------------------------------
# Power curve, NP, IF, TSS helpers (mirror analyse_fit conventions)
# --------------------------------------------------------------------

def peak_power_curve(power: np.ndarray, windows_s: list[int]) -> dict:
    """Best rolling-average power at each window length (seconds)."""
    out = {}
    p = np.nan_to_num(power, nan=0.0)
    for w in windows_s:
        if len(p) < w:
            out[w] = None
            continue
        # uniform rolling mean
        c = np.convolve(p, np.ones(w) / w, mode="valid")
        out[w] = float(c.max())
    return out


def normalized_power(power: np.ndarray) -> float:
    """30s rolling avg → 4th power → mean → 4th root."""
    p = np.nan_to_num(power, nan=0.0)
    if len(p) < 30:
        return float(p.mean()) if len(p) else 0.0
    roll = np.convolve(p, np.ones(30) / 30, mode="valid")
    return float((np.mean(roll ** 4)) ** 0.25)


def variability_index(np_w: float, avg_w: float) -> float:
    return np_w / avg_w if avg_w > 0 else 0.0


def ftp_estimate_from_power(power: np.ndarray) -> dict:
    """FTP estimates from a single endurance ride.

    Three estimators with different assumptions:

    1. **20-min best × 0.95** (Coggan): valid ONLY when the rider actually
       held a max 20-min effort during the ride. On a paced endurance ride
       this severely understates FTP.

    2. **60-min best**: even more conservative — the average power over the
       single best continuous hour. Same caveat as above for a paced ride.

    3. **5-min best × 0.90** (anchor for untested first-power-meter riders):
       on a hilly endurance ride, a sustained climb often produces the only
       genuinely maximal sub-10-min effort. 5-min power on such a climb
       sits roughly at 105–115% of FTP for trained riders. The ×0.90
       multiplier is the conservative midpoint — usually within ±10 W of
       a properly tested FTP for moderately trained riders.

    Returns all three so the reader can pick the appropriate one given
    what they know about the ride context.
    """
    pc = peak_power_curve(power, [60, 300, 1200, 2400, 3600])
    p5  = pc[300]
    p20 = pc[1200]
    p60 = pc[3600]
    estimates = {}
    if p5:
        estimates["from_5min"] = round(p5 * 0.90)
    if p20:
        estimates["from_20min"] = round(p20 * 0.95)
    if p60:
        estimates["from_60min"] = round(p60)
    if p5 and p20 and p60:
        # For paced endurance rides, weight the 5-min estimate heaviest since
        # the longer windows are almost certainly NOT maximal efforts.
        estimates["endurance_ride_consensus"] = round(
            0.6 * p5 * 0.90 + 0.25 * p20 * 0.95 + 0.15 * p60
        )
    return {"peaks": pc, "estimates": estimates}


# --------------------------------------------------------------------
# Per-climb + flat segmentation
# --------------------------------------------------------------------

@dataclass
class Segment:
    label: str
    km_start: float
    km_end: float


def slice_segment(arr: dict, seg: Segment) -> dict:
    """Return a subset of arrays (distance/power/HR/etc.) restricted to a
    given km range. Indices come from arr['distance_m']."""
    d = arr["distance_m"]
    if d is None or len(d) == 0:
        return {}
    mask = (d >= seg.km_start * 1000.0) & (d <= seg.km_end * 1000.0)
    if not np.any(mask):
        return {}
    return {k: (v[mask] if isinstance(v, np.ndarray) else v) for k, v in arr.items()}


def segment_stats(seg_arr: dict) -> dict:
    """Headline metrics for one segment of one rider."""
    if not seg_arr:
        return {}
    p = seg_arr.get("power_w")
    hr = seg_arr.get("hr_bpm")
    cad = seg_arr.get("cadence_rpm")
    spd = seg_arr.get("speed_kmh")
    duration_s = int(len(seg_arr["distance_m"]))
    out = {"duration_s": duration_s, "minutes": round(duration_s / 60.0, 1)}
    if p is not None and len(p):
        p_clean = np.nan_to_num(p, nan=0.0)
        out["avg_w"] = round(float(p_clean.mean()), 0)
        out["np_w"] = round(normalized_power(p_clean), 0)
        out["max_w"] = round(float(p_clean.max()), 0)
    if hr is not None and len(hr):
        hr_clean = np.array([h for h in hr if h is not None and h > 0], dtype=float)
        if len(hr_clean):
            out["avg_hr"] = round(float(hr_clean.mean()), 0)
            out["max_hr"] = round(float(hr_clean.max()), 0)
    if cad is not None and len(cad):
        cad_clean = np.array([c for c in cad if c is not None and c > 30], dtype=float)
        if len(cad_clean):
            out["avg_cad"] = round(float(cad_clean.mean()), 0)
    if spd is not None and len(spd):
        spd_clean = np.array([s for s in spd if s is not None and s > 0], dtype=float)
        if len(spd_clean):
            out["avg_kmh"] = round(float(spd_clean.mean()), 1)
            out["max_kmh"] = round(float(spd_clean.max()), 1)
    # Distance covered
    d = seg_arr["distance_m"]
    if len(d) > 1:
        out["km"] = round((float(d[-1]) - float(d[0])) / 1000.0, 2)
    return out


def detect_flat_segments(arr: dict, min_duration_s: int = 60,
                         max_abs_grade_pct: float = 2.0) -> list[Segment]:
    """Find contiguous segments where |grade| stays under threshold.

    Grade is computed from altitude over a 50m sliding window, NOT the
    instantaneous grade field (which is noisy on FIT records).
    """
    d = arr.get("distance_m")
    e = arr.get("altitude_m")
    if d is None or e is None or len(d) < 100:
        return []
    # 50m window grade
    n = len(d)
    grad = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 25)
        hi = min(n - 1, i + 25)
        if d[hi] - d[lo] > 0:
            grad[i] = (e[hi] - e[lo]) / (d[hi] - d[lo]) * 100
    is_flat = np.abs(grad) <= max_abs_grade_pct
    # Find contiguous runs
    segs = []
    i = 0
    while i < n:
        if is_flat[i]:
            j = i
            while j < n and is_flat[j]:
                j += 1
            # Convert sample-count to seconds (1 Hz assumption — true for both FITs)
            if j - i >= min_duration_s:
                segs.append(Segment(
                    label=f"flat km {d[i]/1000:.2f}–{d[j-1]/1000:.2f}",
                    km_start=float(d[i]) / 1000.0,
                    km_end=float(d[j-1]) / 1000.0,
                ))
            i = j
        else:
            i += 1
    return segs


def detect_flat_attacks(arr: dict, ftp_w: float, threshold_pct: float = 120,
                        min_duration_s: int = 15, max_grade_pct: float = 2.0
                        ) -> list[dict]:
    """Surges on flat: power > threshold_pct * FTP, grade |<2%|, ≥15s."""
    d = arr.get("distance_m")
    p = arr.get("power_w")
    e = arr.get("altitude_m")
    # Power-only / no-baro FITs lack altitude; a zero/blank FTP would make the
    # surge threshold 0 and flag every sample. Bail cleanly in both cases.
    if p is None or d is None or e is None or len(p) < 100 or ftp_w <= 0:
        return []
    n = len(d)
    grad = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 25)
        hi = min(n - 1, i + 25)
        if d[hi] - d[lo] > 0:
            grad[i] = (e[hi] - e[lo]) / (d[hi] - d[lo]) * 100
    is_flat = np.abs(grad) <= max_grade_pct
    threshold = ftp_w * threshold_pct / 100.0
    p_clean = np.nan_to_num(p, nan=0.0)
    is_surge = p_clean > threshold
    in_attack = is_flat & is_surge
    attacks = []
    i = 0
    while i < n:
        if in_attack[i]:
            j = i
            while j < n and in_attack[j]:
                j += 1
            if j - i >= min_duration_s:
                attacks.append({
                    "km": float(d[i]) / 1000.0,
                    "duration_s": j - i,
                    "avg_w": float(p_clean[i:j].mean()),
                    "peak_w": float(p_clean[i:j].max()),
                })
            i = j
        else:
            i += 1
    return attacks


# --------------------------------------------------------------------
# Rider container
# --------------------------------------------------------------------

@dataclass
class RiderRide:
    label: str
    fit_path: Path
    session: dict
    arr: dict   # numpy arrays keyed by 'power','heart_rate','distance_m', etc.
    ftp_w: float
    weight_kg: float = 80.0  # default; rider 80, Thomas est ~75 (placeholder)


def load_rider(label: str, fit_path: Path, ftp_w: float | None = None,
               weight_kg: float | None = None) -> RiderRide:
    sess, recs, _ = parse_fit(str(fit_path))
    arr = to_arrays(recs)
    ftp = ftp_w if ftp_w is not None else float(sess.get("threshold_power") or 0)
    return RiderRide(
        label=label, fit_path=fit_path, session=sess, arr=arr,
        ftp_w=ftp, weight_kg=weight_kg or 80.0,
    )


# --------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------

def fmt_w(x):
    if x is None: return "—"
    return f"{int(x)}"

def fmt_f(x, digits=1):
    if x is None: return "—"
    return f"{x:.{digits}f}"

def render_headline(r1: RiderRide, r2: RiderRide) -> str:
    """Side-by-side session table from the FIT-stored values."""
    rows = [
        ("Distance", f"{r1.session.get('total_distance', 0)/1000:.2f} km",
                     f"{r2.session.get('total_distance', 0)/1000:.2f} km"),
        ("Moving time", f"{r1.session.get('total_timer_time', 0)/3600:.2f} h",
                        f"{r2.session.get('total_timer_time', 0)/3600:.2f} h"),
        ("Elapsed time", f"{r1.session.get('total_elapsed_time', 0)/3600:.2f} h",
                         f"{r2.session.get('total_elapsed_time', 0)/3600:.2f} h"),
        ("Total ascent (baro)", f"{r1.session.get('total_ascent', 0)} m",
                                f"{r2.session.get('total_ascent', 0)} m"),
        ("Stored FTP", f"{r1.session.get('threshold_power', '—')} W",
                       f"{r2.session.get('threshold_power', '—')} W"),
        ("Normalized Power", f"{r1.session.get('normalized_power', '—')} W",
                             f"{r2.session.get('normalized_power', '—')} W"),
        ("Intensity Factor", f"{r1.session.get('intensity_factor', '—')}",
                             f"{r2.session.get('intensity_factor', '—')}"),
        ("TSS", f"{r1.session.get('training_stress_score', '—')}",
                f"{r2.session.get('training_stress_score', '—')}"),
        ("Total work", f"{r1.session.get('total_work', 0)/1000:.0f} kJ",
                       f"{r2.session.get('total_work', 0)/1000:.0f} kJ"),
        ("Avg HR", f"{r1.session.get('avg_heart_rate', '—')} bpm",
                   f"{r2.session.get('avg_heart_rate', '—')} bpm"),
        ("Max HR", f"{r1.session.get('max_heart_rate', '—')} bpm",
                   f"{r2.session.get('max_heart_rate', '—')} bpm"),
        ("Avg power", f"{r1.session.get('avg_power', '—')} W",
                      f"{r2.session.get('avg_power', '—')} W"),
        ("Max power", f"{r1.session.get('max_power', '—')} W",
                      f"{r2.session.get('max_power', '—')} W"),
        ("Avg cadence", f"{r1.session.get('avg_cadence', '—')} rpm",
                        f"{r2.session.get('avg_cadence', '—')} rpm"),
    ]
    out = [f"| Metric | {r1.label} | {r2.label} |",
           "|---|---|---|"]
    for label, a, b in rows:
        out.append(f"| {label} | {a} | {b} |")
    return "\n".join(out)


def render_power_curves(r1: RiderRide, r2: RiderRide) -> str:
    windows = [5, 15, 30, 60, 300, 600, 1200, 1800, 3600]
    labels = ["5s", "15s", "30s", "1m", "5m", "10m", "20m", "30m", "60m"]
    pc1 = peak_power_curve(r1.arr.get("power_w", np.array([])), windows)
    pc2 = peak_power_curve(r2.arr.get("power_w", np.array([])), windows)
    out = [f"| Window | {r1.label} | {r2.label} | Δ (Thomas − Rider) | Δ % |",
           "|---|---|---|---|---|"]
    for w, lab in zip(windows, labels):
        a = pc1.get(w)
        b = pc2.get(w)
        if a is None or b is None:
            out.append(f"| {lab} | {fmt_w(a)} | {fmt_w(b)} | — | — |")
            continue
        delta = b - a
        pct = (delta / a * 100) if a > 0 else 0.0
        out.append(f"| {lab} | {fmt_w(a)} W | {fmt_w(b)} W | {delta:+.0f} W | {pct:+.1f}% |")
    return "\n".join(out)


def render_ftp_estimate(r1: RiderRide, r2: RiderRide) -> str:
    e1 = ftp_estimate_from_power(r1.arr.get("power_w", np.array([])))
    e2 = ftp_estimate_from_power(r2.arr.get("power_w", np.array([])))
    lines = []
    for r, e in [(r1, e1), (r2, e2)]:
        lines.append(f"### {r.label}")
        lines.append("")
        lines.append(f"- Stored FTP (from FIT session): **{r.ftp_w:.0f} W**")
        if "from_5min" in e["estimates"]:
            lines.append(f"- From 5-min best × 0.90 (best estimate for paced "
                         f"endurance rides — anchor on the hardest sustained "
                         f"climb effort): **{e['estimates']['from_5min']:.0f} W** "
                         f"(5-min peak {e['peaks'][300]:.0f} W)")
        if "from_20min" in e["estimates"]:
            lines.append(f"- From 20-min best × 0.95 (Coggan; only valid if a "
                         f"max 20-min effort was actually held): "
                         f"{e['estimates']['from_20min']:.0f} W "
                         f"(20-min peak {e['peaks'][1200]:.0f} W)")
        if "from_60min" in e["estimates"]:
            lines.append(f"- From 60-min best (very conservative on a paced "
                         f"ride): {e['estimates']['from_60min']:.0f} W "
                         f"(60-min peak {e['peaks'][3600]:.0f} W)")
        if "endurance_ride_consensus" in e["estimates"]:
            lines.append(f"- **Endurance-ride weighted consensus: "
                         f"~{e['estimates']['endurance_ride_consensus']:.0f} W**")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------

def efficiency_factor(np_w: float, avg_hr: float) -> float:
    """EF = NP / avg HR. Aerobic fitness proxy; higher is more efficient."""
    return np_w / avg_hr if avg_hr > 0 else 0.0


def peak_speed_curve(speed_kmh: np.ndarray, windows_s: list[int]) -> dict:
    """Best rolling-average speed (km/h) at each window length."""
    out = {}
    s = np.nan_to_num(speed_kmh, nan=0.0)
    for w in windows_s:
        if len(s) < w:
            out[w] = None
            continue
        c = np.convolve(s, np.ones(w) / w, mode="valid")
        out[w] = float(c.max())
    return out


# --------------------------------------------------------------------
# Time-aligned proximity events ("when were we drafting together?")
# --------------------------------------------------------------------

SEMI_TO_DEG = 180.0 / (1 << 31)


def _haversine_m(lat1, lon1, lat2, lon2):
    import math
    R = 6371000.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))


def _load_records_with_timestamps(path: str) -> list[dict]:
    """Raw records with original timestamp (datetime) preserved.

    `analyse_fit.to_arrays` drops the wall-clock timestamp in favour of
    `time_s` (relative). For two-rider alignment we need the wall clock.
    """
    _, recs, _ = parse_fit(path)
    return recs


def align_two_rides(path1: str, path2: str) -> list[dict]:
    """Return list of dicts at common 1-second timestamps containing both
    riders' state: position, power, HR, speed, altitude, distance.

    Drops samples where either rider is missing a critical field
    (lat/lon/speed) so downstream filters don't see garbage.
    """
    r1 = _load_records_with_timestamps(path1)
    r2 = _load_records_with_timestamps(path2)

    def to_dict(recs):
        out = {}
        for r in recs:
            t = r.get("timestamp")
            if t is None:
                continue
            out[t] = r
        return out

    d1 = to_dict(r1)
    d2 = to_dict(r2)
    common = sorted(set(d1.keys()) & set(d2.keys()))

    rows = []
    for t in common:
        a = d1[t]; b = d2[t]
        la1, lo1 = a.get("position_lat"), a.get("position_long")
        la2, lo2 = b.get("position_lat"), b.get("position_long")
        if None in (la1, lo1, la2, lo2):
            continue
        rows.append({
            "t": t,
            "lat1": la1 * SEMI_TO_DEG, "lon1": lo1 * SEMI_TO_DEG,
            "lat2": la2 * SEMI_TO_DEG, "lon2": lo2 * SEMI_TO_DEG,
            "alt1": a.get("enhanced_altitude") or a.get("altitude"),
            "alt2": b.get("enhanced_altitude") or b.get("altitude"),
            "spd1": (a.get("enhanced_speed") or a.get("speed") or 0) * 3.6,
            "spd2": (b.get("enhanced_speed") or b.get("speed") or 0) * 3.6,
            "pwr1": a.get("power"), "pwr2": b.get("power"),
            "hr1":  a.get("heart_rate"), "hr2":  b.get("heart_rate"),
            "dist1": a.get("distance"), "dist2": b.get("distance"),
        })
    return rows


def find_proximity_events(
    rows: list[dict],
    *,
    close_m: float = 50.0,
    min_speed_kmh: float = 25.0,
    max_grade_pct: float = 1.5,
    min_duration_s: int = 15,
    grade_window_s: int = 30,
) -> list[dict]:
    """Sections where both riders were close, fast, and on flat/decline.

    Grade is computed from rider 1's altitude over a centred ±grade_window_s/2
    window (uses the cumulative distance from rider 1). max_grade_pct is
    signed: a positive value here means "uphill no steeper than X" — passing
    a negative value would restrict to declines only. We allow up to +1.5%
    by default so "flat OR decline OR very gentle drag" all qualify.
    """
    if not rows:
        return []

    n = len(rows)
    # Pre-compute distance between riders and grade
    inter_m = [_haversine_m(r["lat1"], r["lon1"], r["lat2"], r["lon2"])
               for r in rows]
    # Grade from rider 1's altitude + distance
    half = max(5, grade_window_s // 2)
    grades = [0.0] * n
    d1 = [r["dist1"] for r in rows]
    a1 = [r["alt1"] for r in rows]
    for i in range(n):
        lo = max(0, i - half); hi = min(n - 1, i + half)
        if d1[lo] is None or d1[hi] is None or a1[lo] is None or a1[hi] is None:
            continue
        dd = d1[hi] - d1[lo]
        if dd > 0:
            grades[i] = (a1[hi] - a1[lo]) / dd * 100.0

    # Per-sample pass/fail
    passes = []
    for i, r in enumerate(rows):
        ok = (inter_m[i] <= close_m
              and r["spd1"] >= min_speed_kmh
              and r["spd2"] >= min_speed_kmh
              and grades[i] <= max_grade_pct)
        passes.append(ok)

    # Coalesce contiguous runs into events
    events = []
    i = 0
    while i < n:
        if passes[i]:
            j = i
            while j < n and passes[j]:
                j += 1
            if j - i >= min_duration_s:
                # Aggregate
                seg = rows[i:j]
                seg_grade = grades[i:j]
                seg_inter = inter_m[i:j]
                seg_p1 = [r["pwr1"] for r in seg if r["pwr1"] is not None]
                seg_p2 = [r["pwr2"] for r in seg if r["pwr2"] is not None]
                seg_s1 = [r["spd1"] for r in seg]
                seg_s2 = [r["spd2"] for r in seg]
                seg_hr1 = [r["hr1"] for r in seg if r["hr1"] is not None]
                seg_hr2 = [r["hr2"] for r in seg if r["hr2"] is not None]
                events.append({
                    "start_t": seg[0]["t"],
                    "end_t":   seg[-1]["t"],
                    "duration_s": j - i,
                    "km_start": (seg[0]["dist1"] or 0) / 1000.0,
                    "km_end":   (seg[-1]["dist1"] or 0) / 1000.0,
                    "avg_inter_m": float(np.mean(seg_inter)),
                    "min_inter_m": float(np.min(seg_inter)),
                    "max_inter_m": float(np.max(seg_inter)),
                    "avg_grade_pct": float(np.mean(seg_grade)),
                    "avg_kmh_r1": float(np.mean(seg_s1)),
                    "avg_kmh_r2": float(np.mean(seg_s2)),
                    "max_kmh_r1": float(np.max(seg_s1)),
                    "max_kmh_r2": float(np.max(seg_s2)),
                    "avg_pwr_r1": float(np.mean(seg_p1)) if seg_p1 else 0.0,
                    "avg_pwr_r2": float(np.mean(seg_p2)) if seg_p2 else 0.0,
                    "max_pwr_r1": float(np.max(seg_p1)) if seg_p1 else 0.0,
                    "max_pwr_r2": float(np.max(seg_p2)) if seg_p2 else 0.0,
                    "avg_hr_r1":  float(np.mean(seg_hr1)) if seg_hr1 else 0.0,
                    "avg_hr_r2":  float(np.mean(seg_hr2)) if seg_hr2 else 0.0,
                })
            i = j
        else:
            i += 1
    return events


def find_chase_episodes(rows: list[dict], events: list[dict]) -> list[dict]:
    """Score events by chase-pattern signature.

    A 'chase' is a fast-and-close event where the inter-rider distance
    varies a lot — closing in, falling back, closing again. Looks for
    events with the longest duration AND a large min→max inter-distance
    spread.
    """
    scored = []
    for ev in events:
        spread = ev["max_inter_m"] - ev["min_inter_m"]
        score = ev["duration_s"] * 0.5 + spread * 1.0
        scored.append({**ev, "spread_m": spread, "score": score})
    scored.sort(key=lambda x: -x["score"])
    return scored


def time_in_zones(power: np.ndarray, ftp: float) -> dict:
    """Time-in-zones (Coggan), as percentage of total non-zero samples."""
    if power is None or len(power) == 0 or ftp <= 0:
        return {}
    p = power[power > 0]
    if len(p) == 0:
        return {}
    bins = {
        "Z1 (<55%)":       (0,        0.55 * ftp),
        "Z2 (55–75%)":     (0.55*ftp, 0.75 * ftp),
        "Z3 (75–90%)":     (0.75*ftp, 0.90 * ftp),
        "Z4 (90–105%)":    (0.90*ftp, 1.05 * ftp),
        "Z5 (105–120%)":   (1.05*ftp, 1.20 * ftp),
        "Z6 (120–150%)":   (1.20*ftp, 1.50 * ftp),
        "Z7 (>150%)":      (1.50*ftp, float("inf")),
    }
    total = len(p)
    out = {}
    for name, (lo, hi) in bins.items():
        n = np.sum((p > lo) & (p <= hi))
        out[name] = round(100.0 * n / total, 1)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compare two riders' FIT files from the same ride. "
                    "With --peer <name>, looks up labels/FTP/weight/source "
                    "from USER_PROFILE.md's peer_<name>: registry so common "
                    "flags don't need re-passing.",
    )
    p.add_argument("rider_fit", help="Path to rider's FIT")
    p.add_argument("peer_fit",  help="Path to peer's FIT")
    p.add_argument("--peer", help="Peer short name (looks up peer_<name>: in "
                                  "USER_PROFILE.md frontmatter). Overrides "
                                  "individual flags when present.")
    p.add_argument("--rider-label", default=None,
                   help="Defaults to USER_PROFILE identity.name if available")
    p.add_argument("--peer-label",  default=None)
    p.add_argument("--rider-weight", type=float, default=None,
                   help="Defaults to USER_PROFILE body.weight_kg")
    p.add_argument("--peer-weight",  type=float, default=None,
                   help="Defaults to peer registry weight_kg or 75.0")
    p.add_argument("--gpx", help="Optional GPX of the route for canonical climb spans")
    p.add_argument("--peer-ftp-source", default=None,
                   help="How the peer's stored FTP was set: 'test', 'garmin-auto', "
                        "'self-declared', 'unknown'. Defaults to registry value.")
    p.add_argument("--out", help="Write markdown to this path (otherwise stdout)")
    args = p.parse_args()

    # Profile + peer registry lookups. CLI flags always win when explicitly set.
    try:
        from profile import load_profile, load_peer
        prof = load_profile()
        rider_name_default = prof.get("identity", {}).get("name", "Rider")
        rider_weight_default = float(prof.get("body", {}).get("weight_kg", 80.0))
    except Exception:
        prof = None
        rider_name_default = "Rider"
        rider_weight_default = 80.0

    peer_cfg = None
    if args.peer:
        try:
            peer_cfg = load_peer(args.peer)
        except Exception:
            peer_cfg = None
        if not peer_cfg:
            print(f"WARNING: peer '{args.peer}' not in USER_PROFILE.md "
                  f"registry — falling back to CLI flags / defaults.",
                  file=sys.stderr)
            peer_cfg = {}

    # Resolve effective values: CLI flag > peer registry > defaults
    args.rider_label = args.rider_label or rider_name_default
    args.peer_label = (args.peer_label
                      or (peer_cfg.get("label") if peer_cfg else None)
                      or (args.peer.capitalize() if args.peer else "Peer"))
    args.rider_weight = (args.rider_weight if args.rider_weight is not None
                         else rider_weight_default)
    args.peer_weight = (args.peer_weight if args.peer_weight is not None
                        else (float(peer_cfg.get("weight_kg", 75.0))
                              if peer_cfg else 75.0))
    args.peer_ftp_source = (args.peer_ftp_source
                            or (peer_cfg.get("ftp_source") if peer_cfg else None)
                            or "unknown")

    # Optional FTP override from the peer registry — the peer's FIT-stored
    # FTP can be unreliable (e.g. Garmin auto-FTP runs hot). When the
    # registry has a calibrated `ftp_w` it wins for analysis purposes;
    # the FIT-stored value is still surfaced in the FTP-estimation section.
    peer_ftp_override = None
    if peer_cfg and "ftp_w" in peer_cfg:
        peer_ftp_override = float(peer_cfg["ftp_w"])

    r1 = load_rider(args.rider_label, Path(args.rider_fit),
                    weight_kg=args.rider_weight)
    r2 = load_rider(args.peer_label,  Path(args.peer_fit),
                    ftp_w=peer_ftp_override,
                    weight_kg=args.peer_weight)

    if peer_ftp_override is not None:
        stored_in_fit = float(r2.session.get("threshold_power") or 0)
        print(f"Using registry FTP {peer_ftp_override:.0f} W for {r2.label} "
              f"(FIT-stored: {stored_in_fit:.0f} W, source: {args.peer_ftp_source})",
              file=sys.stderr)

    # Climb spans: prefer verify_climbs output if --gpx given; else use
    # find_climbs on the rider's FIT (already-truth for that rider).
    climb_spans: list[Segment] = []
    if args.gpx:
        from local_dem import LocalDEM
        from verify_climbs import verify_route
        dem = LocalDEM(Path.home() / "cycling-coach-dem")
        report = verify_route(Path(args.gpx), dem)
        for c in list(report.climbs) + list(report.missed_climbs):
            climb_spans.append(Segment(
                label=f"{c.name} (peak {c.verified_peak_pct:.1f}%)",
                km_start=c.km_start, km_end=c.km_end,
            ))
    else:
        from analyse_gpx import find_climbs
        a = r1.arr
        cs = find_climbs(a["distance_m"], a["altitude_m"])
        for i, c in enumerate(cs, 1):
            climb_spans.append(Segment(
                label=f"Climb {i} (km {c['start_km']:.2f}–{c['end_km']:.2f})",
                km_start=c["start_km"], km_end=c["end_km"],
            ))

    # Per-climb stats
    climb_rows = []
    for seg in climb_spans:
        s1 = segment_stats(slice_segment(r1.arr, seg))
        s2 = segment_stats(slice_segment(r2.arr, seg))
        climb_rows.append((seg, s1, s2))

    # Flats — found from rider's altitude trace (Wahoo barometric)
    flat_segs = detect_flat_segments(r1.arr, min_duration_s=60,
                                     max_abs_grade_pct=2.0)
    # Aggregate flat stats across all flat segs (combined)
    flat_total_s = sum((s.km_end - s.km_start) * 1000 / 5.5  # rough placeholder; below
                       for s in flat_segs)  # not used in output
    flats_combined_r1 = []
    flats_combined_r2 = []
    for seg in flat_segs:
        s1 = segment_stats(slice_segment(r1.arr, seg))
        s2 = segment_stats(slice_segment(r2.arr, seg))
        flats_combined_r1.append(s1)
        flats_combined_r2.append(s2)
    def mean_metric(stats_list, key):
        vals = [s.get(key) for s in stats_list if s.get(key) is not None]
        return float(np.mean(vals)) if vals else None
    flat_summary_r1 = {
        "n": len(flats_combined_r1),
        "avg_w": mean_metric(flats_combined_r1, "avg_w"),
        "np_w": mean_metric(flats_combined_r1, "np_w"),
        "avg_kmh": mean_metric(flats_combined_r1, "avg_kmh"),
        "avg_hr": mean_metric(flats_combined_r1, "avg_hr"),
    }
    flat_summary_r2 = {
        "n": len(flats_combined_r2),
        "avg_w": mean_metric(flats_combined_r2, "avg_w"),
        "np_w": mean_metric(flats_combined_r2, "np_w"),
        "avg_kmh": mean_metric(flats_combined_r2, "avg_kmh"),
        "avg_hr": mean_metric(flats_combined_r2, "avg_hr"),
    }

    # Flat attacks (per rider, using their own stored FTP)
    attacks_r1 = detect_flat_attacks(r1.arr, ftp_w=r1.ftp_w)
    attacks_r2 = detect_flat_attacks(r2.arr, ftp_w=r2.ftp_w)

    # --- Render ---
    md = []
    md.append(f"# Rider vs {r2.label} — Lost Lane #21 (2026-06-13)")
    md.append("")
    md.append("Side-by-side ride comparison from the two FIT recordings of the "
              "same route, ridden together.")
    md.append("")
    md.append(f"_{r1.label}: `{r1.fit_path.name}`_  ")
    md.append(f"_{r2.label}: `{r2.fit_path.name}`_")
    md.append("")

    md.append("## Headline metrics")
    md.append("")
    md.append(render_headline(r1, r2))
    md.append("")

    md.append("## Power curve")
    md.append("")
    md.append(render_power_curves(r1, r2))
    md.append("")

    md.append("## FTP estimation")
    md.append("")
    if args.peer_ftp_source == "garmin-auto":
        md.append(f"> ⚠️ **{r2.label}'s stored FTP is Garmin's auto-estimate, "
                  f"not a test value.** Garmin auto-FTP runs high for new "
                  f"power-meter users — it extrapolates from short max efforts. "
                  f"The 5-min anchored estimate below is more reliable for a "
                  f"first-power-meter endurance ride.")
        md.append("")
    md.append(render_ftp_estimate(r1, r2))

    # Efficiency Factor (NP / avg HR) — aerobic-fitness proxy
    ef1 = efficiency_factor(
        r1.session.get("normalized_power", 0),
        r1.session.get("avg_heart_rate", 0),
    )
    ef2 = efficiency_factor(
        r2.session.get("normalized_power", 0),
        r2.session.get("avg_heart_rate", 0),
    )
    md.append("## Aerobic efficiency (EF = NP / avg HR)")
    md.append("")
    md.append(f"| Rider | NP | Avg HR | EF |")
    md.append("|---|---|---|---|")
    md.append(f"| {r1.label} | {r1.session.get('normalized_power')} W | "
              f"{r1.session.get('avg_heart_rate')} bpm | **{ef1:.2f}** |")
    md.append(f"| {r2.label} | {r2.session.get('normalized_power')} W | "
              f"{r2.session.get('avg_heart_rate')} bpm | **{ef2:.2f}** |")
    md.append("")
    ef_delta = f"{ef2-ef1:+.2f}"
    ef_pct = f" ({(ef2/ef1-1)*100:+.0f}%)" if ef1 > 0 else ""
    md.append(f"_EF is a clean aerobic-fitness proxy: higher = more power per "
              f"heartbeat. A trained endurance cyclist typically sits 1.20–1.50; "
              f"developing aerobic base sits 0.85–1.10. **Delta: {ef_delta}"
              f"{ef_pct}.**_")
    md.append("")

    # Time-in-zones
    md.append("## Time-in-zones (vs each rider's stored FTP)")
    md.append("")
    md.append(f"| Zone | {r1.label} (FTP {r1.ftp_w:.0f}W) | "
              f"{r2.label} (FTP {r2.ftp_w:.0f}W) |")
    md.append("|---|---|---|")
    tiz1 = time_in_zones(r1.arr.get("power_w"), r1.ftp_w)
    tiz2 = time_in_zones(r2.arr.get("power_w"), r2.ftp_w)
    for zone in ["Z1 (<55%)", "Z2 (55–75%)", "Z3 (75–90%)",
                 "Z4 (90–105%)", "Z5 (105–120%)", "Z6 (120–150%)",
                 "Z7 (>150%)"]:
        a = tiz1.get(zone, 0.0)
        b = tiz2.get(zone, 0.0)
        md.append(f"| {zone} | {a:.1f}% | {b:.1f}% |")
    md.append("")

    md.append("## Per-climb comparison")
    md.append("")
    md.append(f"| Climb | {r1.label} (avg/NP/max W, avg HR, kmh) | "
              f"{r2.label} (avg/NP/max W, avg HR, kmh) |")
    md.append("|---|---|---|")
    for seg, s1, s2 in climb_rows:
        def fmt(s):
            if not s:
                return "—"
            return (f"{fmt_w(s.get('avg_w'))}/{fmt_w(s.get('np_w'))}/"
                    f"{fmt_w(s.get('max_w'))} W, "
                    f"{fmt_w(s.get('avg_hr'))} bpm, "
                    f"{fmt_f(s.get('avg_kmh'))} kmh")
        md.append(f"| {seg.label} | {fmt(s1)} | {fmt(s2)} |")
    md.append("")

    md.append("## Flat-section comparison")
    md.append("")
    md.append(f"_{flat_summary_r1['n']} flat segments detected on rider's altitude trace "
              f"(|grade|≤2%, ≥60s)._")
    md.append("")
    md.append(f"| Metric | {r1.label} | {r2.label} |")
    md.append("|---|---|---|")
    for key, label in [("avg_w", "Mean avg-W across flats"),
                       ("np_w", "Mean NP across flats"),
                       ("avg_kmh", "Mean avg-kmh across flats"),
                       ("avg_hr", "Mean avg-HR across flats")]:
        a, b = flat_summary_r1.get(key), flat_summary_r2.get(key)
        md.append(f"| {label} | {fmt_f(a)} | {fmt_f(b)} |")
    md.append("")

    md.append("## Flat attacks")
    md.append("")
    md.append(f"_Surges defined as power > 120% of each rider's own stored FTP, "
              f"sustained ≥15s, on grade |≤2%|._")
    md.append("")
    md.append(f"- **{r1.label}**: {len(attacks_r1)} flat attacks")
    if attacks_r1:
        peak = max(a["peak_w"] for a in attacks_r1)
        longest = max(a["duration_s"] for a in attacks_r1)
        md.append(f"  - peak attack: {peak:.0f} W; longest: {longest}s")
    md.append(f"- **{r2.label}**: {len(attacks_r2)} flat attacks")
    if attacks_r2:
        peak = max(a["peak_w"] for a in attacks_r2)
        longest = max(a["duration_s"] for a in attacks_r2)
        md.append(f"  - peak attack: {peak:.0f} W; longest: {longest}s")
    md.append("")

    output = "\n".join(md) + "\n"
    if args.out:
        Path(args.out).write_text(output)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
