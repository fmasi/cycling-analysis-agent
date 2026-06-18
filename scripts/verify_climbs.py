"""Climb verification orchestrator.

Re-samples each candidate climb from analyse_gpx against a high-fidelity
elevation source (LocalDEM, GPXZ fallback) and produces a Fidelity Report
that flags peak-gradient underestimation and missed climbs.
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Sibling-script imports (analyse_gpx) — make scripts/ importable as a flat dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@dataclass
class ClimbVerification:
    name: str
    km_start: float
    km_end: float
    gpx_peak_pct: float
    verified_peak_pct: float
    delta_pp: float
    length_above_8: float
    length_above_10: float
    length_above_12: float
    length_above_14: float
    fallback_used: bool
    # Stash the verified densified profile so downstream renderers can stitch
    # without re-fetching. route_m is cumulative metres along the full route.
    verified_route_m: list[float] = field(default_factory=list)
    verified_elevs: list[float] = field(default_factory=list)
    # Re-computed pacing on the verified peak/avg/gain (physics_model output).
    # Empty when physics couldn't be computed (no verified samples).
    verified_pacing: dict = field(default_factory=dict)
    # Mean-max gradient curve — steepest sustained grade over fixed windows.
    # Keys: "peak_25m", "peak_100m", "peak_500m", "peak_1km". Value is None
    # when the climb is shorter than the window.
    mean_max: dict = field(default_factory=dict)
    # Walls: contiguous sections >= 10% lasting >= 30m. Each entry:
    # {"offset_m": dist from climb start, "length_m", "peak_pct", "pct_in"}.
    walls: list = field(default_factory=list)


@dataclass
class FidelityReport:
    route_name: str
    backend: str
    coverage_pct: float
    climbs: list[ClimbVerification]
    missed_climbs: list["ClimbVerification"] = field(default_factory=list)
    verdict: str = "safe"
    # Stitched profile (Petrasova-blended), populated by verify_route when
    # any climb has verified samples. Same length: stitched_dists / stitched_elevs.
    stitched_dists: list[float] = field(default_factory=list)
    stitched_elevs: list[float] = field(default_factory=list)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def densify_polyline(
    coords: list[tuple[float, float]], stride_m: float
) -> list[tuple[float, float]]:
    """Insert intermediate points so consecutive samples are <= stride_m apart."""
    if len(coords) < 2:
        return list(coords)
    out: list[tuple[float, float]] = [coords[0]]
    for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
        d = haversine_m(la1, lo1, la2, lo2)
        n = max(1, int(math.ceil(d / stride_m)))
        for k in range(1, n):
            t = k / n
            out.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
        out.append((la2, lo2))
    return out


def smoothed_grades(
    elevs: list[float], dists: list[float], window_m: float = 30.0
) -> list[float]:
    """Per-point gradient % over a window_m centred span."""
    n = len(elevs)
    g = [0.0] * n
    for i in range(n):
        j = i
        while j > 0 and dists[i] - dists[j] < window_m:
            j -= 1
        k = i
        while k < n - 1 and dists[k] - dists[i] < window_m:
            k += 1
        dd = dists[k] - dists[j]
        if dd < 5.0:
            g[i] = 0.0
        else:
            g[i] = 100.0 * (elevs[k] - elevs[j]) / dd
    return g


def longest_run_above(
    grades: list[float], dists: list[float], threshold: float
) -> float:
    """Length (m) of the longest contiguous run with grade >= threshold."""
    best = 0.0
    start: Optional[int] = None
    for i, g in enumerate(grades):
        if g >= threshold:
            if start is None:
                start = i
            anchor = start - 1 if start > 0 else start
            run = dists[i] - dists[anchor]
            if run > best:
                best = run
        else:
            start = None
    return best


def mean_max_grade(
    elevs: list[float], dists: list[float], window_m: float
) -> Optional[float]:
    """Steepest sustained gradient (%) over any window_m-long sub-segment.

    Spatial analogue of a mean-max power curve: slide a fixed-length window
    along the profile, take the max gradient. Returns None if the profile
    is shorter than the window.
    """
    n = len(elevs)
    if n < 2 or dists[-1] - dists[0] < window_m:
        return None
    best = -float("inf")
    j = 0
    for i in range(n):
        if j <= i:
            j = i + 1
        while j < n and dists[j] - dists[i] < window_m:
            j += 1
        if j >= n:
            break
        span = dists[j] - dists[i]
        if span <= 0:
            continue
        g = 100.0 * (elevs[j] - elevs[i]) / span
        if g > best:
            best = g
    return best if best > -float("inf") else None


def detect_walls(
    grades: list[float],
    dists: list[float],
    threshold_pct: float = 10.0,
    min_length_m: float = 30.0,
    total_length_m: Optional[float] = None,
) -> list[dict]:
    """Find contiguous sections where smoothed gradient stays >= threshold.

    Returns list of dicts with offset_m (from start), length_m, peak_pct,
    pct_in (fraction of total length where the wall starts).
    """
    if not grades or not dists or len(grades) != len(dists):
        return []
    total = total_length_m if total_length_m is not None else (dists[-1] - dists[0])
    walls: list[dict] = []
    n = len(grades)
    i = 0
    while i < n:
        if grades[i] >= threshold_pct:
            j = i
            while j < n and grades[j] >= threshold_pct:
                j += 1
            seg_len = dists[j - 1] - dists[i]
            if seg_len >= min_length_m:
                walls.append({
                    "offset_m": dists[i] - dists[0],
                    "length_m": seg_len,
                    "peak_pct": max(grades[i:j]),
                    "pct_in": (
                        100.0 * (dists[i] - dists[0]) / total if total > 0 else 0.0
                    ),
                })
            i = j
        else:
            i += 1
    return walls


def classify_verdict(deltas: list[float], missed: int) -> str:
    if missed > 0:
        return "high"
    worst = max(deltas) if deltas else 0.0
    if worst > 2.0:
        return "high"
    if worst > 1.0:
        return "minor"
    return "safe"


# Z3 endurance target — matches the constant used in analyse_gpx's pacing block.
_Z3_POWER_W = 130


def _compute_pacing(dists, elevs, peak_pct: float) -> dict:
    """Per-climb pacing on verified inputs. Returns {} on degenerate data."""
    if not dists or not elevs or len(dists) < 2:
        return {}
    from physics_model import (
        predict_speed, vam_at_power, power_for_60rpm_in_lowest_gear,
        FTP, MAP_WORKING,
    )

    length_m = dists[-1] - dists[0]
    gain_m = elevs[-1] - elevs[0]
    if length_m <= 0:
        return {}
    avg_pct = 100.0 * gain_m / length_m

    v_ftp = predict_speed(FTP, avg_pct)
    v_map = predict_speed(MAP_WORKING, avg_pct)
    v_z3 = predict_speed(_Z3_POWER_W, avg_pct)
    return {
        "length_m": length_m,
        "gain_m": gain_m,
        "avg_pct": avg_pct,
        "peak_pct": peak_pct,
        "speed_ftp_kmh": v_ftp,
        "speed_map_kmh": v_map,
        "speed_z3_kmh": v_z3,
        "duration_ftp_min": ((length_m / 1000.0) / v_ftp * 60.0) if v_ftp > 0 else 0.0,
        "duration_map_min": ((length_m / 1000.0) / v_map * 60.0) if v_map > 0 else 0.0,
        "vam_ftp": vam_at_power(FTP, avg_pct),
        "survival_w": power_for_60rpm_in_lowest_gear(peak_pct),
    }


def _verify_one_climb(
    climb: dict,
    route_lats,
    route_lons,
    route_dists,
    dem,
    fallback=None,
    stride_m: float = 5.0,
    map_match: bool = True,
) -> ClimbVerification:
    """Resample a single climb against the DEM and produce a ClimbVerification.

    NaN-fill via the optional fallback API. If a coord is uncovered by the DEM
    AND no fallback is configured, the gap is interpolated from neighbouring
    sampled points so the gradient computation does not crash.

    If `map_match` is True (default), GPX coords inside the climb are snapped
    to OSRM road geometry before densification — eliminates the wandering-
    off-road error on curves. Falls back to raw coords if OSRM is unreachable.
    """
    s_m = float(climb["start_km"]) * 1000.0
    e_m = float(climb["end_km"]) * 1000.0

    # Pick the route trackpoints inside [s_m, e_m].
    coords: list[tuple[float, float]] = []
    for lat, lon, d in zip(route_lats, route_lons, route_dists):
        if s_m <= float(d) <= e_m:
            coords.append((float(lat), float(lon)))
    if len(coords) < 2:
        # Degenerate climb — fall back to the two nearest trackpoints.
        coords = [
            (float(route_lats[0]), float(route_lons[0])),
            (float(route_lats[-1]), float(route_lons[-1])),
        ]

    if map_match:
        try:
            from map_match import match_coords
            coords = match_coords(coords)
        except Exception:
            pass  # graceful fallback to raw coords

    densified = densify_polyline(coords, stride_m=stride_m)
    elevs: list[Optional[float]] = [dem.sample(la, lo) for la, lo in densified]

    fallback_used = False
    missing_idx = [i for i, e in enumerate(elevs) if e is None]
    if missing_idx and fallback is not None:
        try:
            missing_coords = [densified[i] for i in missing_idx]
            filled = fallback.sample_polyline(missing_coords)
            for i, v in zip(missing_idx, filled):
                if v is not None:
                    elevs[i] = float(v)
            fallback_used = True
            missing_idx = [i for i, e in enumerate(elevs) if e is None]
        except Exception:
            # Fallback unavailable / rate-limited — fall through to interp.
            pass

    if missing_idx:
        # Linear-interpolate remaining gaps from nearest valid neighbours.
        valid_idx = [i for i, e in enumerate(elevs) if e is not None]
        if not valid_idx:
            # Total miss — return a zeroed verification so the run continues.
            return ClimbVerification(
                name=f"km {climb['start_km']:.2f}",
                km_start=float(climb["start_km"]),
                km_end=float(climb["end_km"]),
                gpx_peak_pct=float(climb.get("max_grad_pct", 0.0)),
                verified_peak_pct=0.0,
                delta_pp=0.0,
                length_above_8=0.0,
                length_above_10=0.0,
                length_above_12=0.0,
                length_above_14=0.0,
                fallback_used=fallback_used,
            )
        for i in missing_idx:
            # Find nearest valid before and after.
            before = max((j for j in valid_idx if j < i), default=None)
            after = min((j for j in valid_idx if j > i), default=None)
            if before is not None and after is not None:
                t = (i - before) / (after - before)
                elevs[i] = elevs[before] * (1 - t) + elevs[after] * t
            elif before is not None:
                elevs[i] = elevs[before]
            else:
                elevs[i] = elevs[after]  # type: ignore[index]

    elevs_f: list[float] = [float(e) for e in elevs]  # type: ignore[arg-type]

    # Cumulative distances along the densified polyline.
    dists = [0.0]
    for (la1, lo1), (la2, lo2) in zip(densified, densified[1:]):
        dists.append(dists[-1] + haversine_m(la1, lo1, la2, lo2))

    grades = smoothed_grades(elevs_f, dists, window_m=30.0)
    verified_peak = max(grades) if grades else 0.0
    gpx_peak = float(climb.get("max_grad_pct", 0.0))

    # Mean-max gradient curve over standard windows. None when the climb
    # is shorter than the window — caller renders "—".
    mean_max = {
        "peak_25m": mean_max_grade(elevs_f, dists, 25.0),
        "peak_100m": mean_max_grade(elevs_f, dists, 100.0),
        "peak_500m": mean_max_grade(elevs_f, dists, 500.0),
        "peak_1km": mean_max_grade(elevs_f, dists, 1000.0),
    }

    # Wall detection — use a finer 15m smoothing so we don't blur the
    # entrance/exit of short steep sections.
    wall_grades = smoothed_grades(elevs_f, dists, window_m=15.0)
    walls = detect_walls(wall_grades, dists)

    # Convert local densified dists to absolute route-m offsets so the
    # stitcher can place them correctly.
    route_m = [s_m + d for d in dists]

    # Pacing recompute on verified gradients (mirrors analyse_gpx's per-climb
    # pacing block; uses verified avg + verified peak as physics inputs).
    verified_pacing = _compute_pacing(dists, elevs_f, verified_peak)

    return ClimbVerification(
        name=f"km {climb['start_km']:.2f}",
        km_start=float(climb["start_km"]),
        km_end=float(climb["end_km"]),
        gpx_peak_pct=gpx_peak,
        verified_peak_pct=verified_peak,
        delta_pp=verified_peak - gpx_peak,
        length_above_8=longest_run_above(grades, dists, 8.0),
        length_above_10=longest_run_above(grades, dists, 10.0),
        length_above_12=longest_run_above(grades, dists, 12.0),
        length_above_14=longest_run_above(grades, dists, 14.0),
        fallback_used=fallback_used,
        verified_route_m=route_m,
        verified_elevs=elevs_f,
        verified_pacing=verified_pacing,
        mean_max=mean_max,
        walls=walls,
    )


def _sample_route_dem(
    lats, lons, dem, stride_m: float = 25.0, fallback=None,
) -> tuple[list[tuple[float, float]], list[float], list[float], float]:
    """Densify route at `stride_m`, sample DEM at every point, gap-fill.

    Returns `(densified_coords, cum_dists_m, elevs_m, coverage_pct)`.
    `coverage_pct` reflects DEM coverage BEFORE any fallback fill — same
    semantic as the previous standalone coverage sweep.
    """
    coords = [(float(la), float(lo)) for la, lo in zip(lats, lons)]
    densified = densify_polyline(coords, stride_m=stride_m)
    if not densified:
        return [], [], [], 0.0

    elevs_raw = [dem.sample(la, lo) for la, lo in densified]
    covered = sum(1 for e in elevs_raw if e is not None)
    coverage_pct = 100.0 * covered / len(elevs_raw)

    missing_idx = [i for i, e in enumerate(elevs_raw) if e is None]
    if missing_idx and fallback is not None and getattr(fallback, "configured", False):
        try:
            filled = fallback.sample_polyline([densified[i] for i in missing_idx])
            for i, v in zip(missing_idx, filled):
                if v is not None:
                    elevs_raw[i] = float(v)
        except Exception:
            pass

    valid_idx = [i for i, e in enumerate(elevs_raw) if e is not None]
    if len(valid_idx) < 2:
        return densified, [], [], coverage_pct

    elevs: list[float] = []
    for i, e in enumerate(elevs_raw):
        if e is not None:
            elevs.append(float(e))
        else:
            before = max((j for j in valid_idx if j < i), default=None)
            after = min((j for j in valid_idx if j > i), default=None)
            if before is not None and after is not None:
                t = (i - before) / (after - before)
                elevs.append(
                    float(elevs_raw[before]) * (1 - t)
                    + float(elevs_raw[after]) * t
                )
            elif before is not None:
                elevs.append(float(elevs_raw[before]))
            elif after is not None:
                elevs.append(float(elevs_raw[after]))
            else:
                elevs.append(0.0)

    dists = [0.0]
    for (la1, lo1), (la2, lo2) in zip(densified, densified[1:]):
        dists.append(dists[-1] + haversine_m(la1, lo1, la2, lo2))

    return densified, dists, elevs, coverage_pct


def detect_missed_climbs(
    route_lats,
    route_lons,
    route_dists,
    known_climbs: list[dict],
    dem,
    stride_m: float = 25.0,
    min_length_m: float = 300.0,
    min_grade_pct: float = 1.5,
    min_gain_m: float = 20.0,
    fallback=None,
    presampled: tuple[list, list[float], list[float], float] | None = None,
) -> list[ClimbVerification]:
    """Walk the entire route and flag rising segments not in `known_climbs`.

    Two-pass:
    1. Coarse sweep at `stride_m` (25m) with 100m-window smoothing finds
       candidate km-ranges where the smoothed grade exceeds `min_grade_pct`.
    2. Each candidate is then handed to `_verify_one_climb` for a fine
       re-sample at 5m stride / 30m smoothing — so missed climbs get the
       same hi-fi treatment (accurate peak, mean-max curve, walls, hi-fi
       pacing) as declared climbs. Pass 1 alone reports the coarsely-
       smoothed peak, which under-states walls inside the climb.

    Segments overlapping any known climb's [start_km, end_km] are dropped
    before Pass 2 — we don't pay the fine-resample cost twice.

    If `fallback` is supplied and configured, points uncovered by `dem`
    are filled in one batched API call before Pass 1 gradient computation.
    """
    if len(route_lats) < 2:
        return []

    if presampled is not None:
        _, dists, elevs, _ = presampled
    else:
        _, dists, elevs, _ = _sample_route_dem(
            route_lats, route_lons, dem, stride_m=stride_m, fallback=fallback,
        )
    if len(elevs) < 2:
        return []

    grades = smoothed_grades(elevs, dists, window_m=100.0)

    # Find contiguous runs above min_grade_pct.
    candidates: list[dict] = []
    n = len(grades)
    i = 0
    while i < n:
        if grades[i] >= min_grade_pct:
            j = i
            while j < n and grades[j] >= min_grade_pct:
                j += 1
            seg_len = dists[j - 1] - dists[i]
            if seg_len >= min_length_m:
                gain = elevs[j - 1] - elevs[i]
                if gain < min_gain_m:
                    i = j
                    continue
                avg = (gain / seg_len * 100.0) if seg_len > 0 else 0.0
                peak = max(grades[i:j]) if j > i else 0.0
                candidates.append(
                    {
                        "start_km": dists[i] / 1000.0,
                        "end_km": dists[j - 1] / 1000.0,
                        "length_m": seg_len,
                        "gain_m": gain,
                        "avg_grad_pct": avg,
                        "peak_grad_pct": peak,
                    }
                )
            i = j
        else:
            i += 1

    # Drop candidates overlapping any known climb (by km range).
    def _overlaps(a_lo, a_hi, b_lo, b_hi):
        return not (a_hi < b_lo or b_hi < a_lo)

    non_overlapping: list[dict] = []
    for c in candidates:
        overlap = any(
            _overlaps(c["start_km"], c["end_km"],
                      float(k["start_km"]), float(k["end_km"]))
            for k in known_climbs
        )
        if not overlap:
            non_overlapping.append(c)

    # Pass 2: fine re-sample each candidate. Each candidate becomes a full
    # ClimbVerification — same shape as declared climbs, so downstream
    # render code can render both lists uniformly.
    verified_missed: list[ClimbVerification] = []
    for c in non_overlapping:
        climb_dict = {
            "start_km": c["start_km"],
            "end_km": c["end_km"],
            # Seed gpx_peak from the coarse-pass peak; the verifier reports
            # this as the "before" number in the per-climb table so the
            # rider can see how much the fine pass pulled out.
            "max_grad_pct": c.get("peak_grad_pct", 0.0),
        }
        cv = _verify_one_climb(
            climb_dict, route_lats, route_lons, route_dists,
            dem, fallback=fallback,
        )
        verified_missed.append(cv)
    return verified_missed


def verify_route(gpx_path: Path, dem, fallback=None) -> FidelityReport:
    """Top-level orchestrator: parse GPX, verify each climb, sweep for missed."""
    from analyse_gpx import parse_gpx, find_climbs

    parsed = parse_gpx(str(gpx_path))
    if parsed is None:
        return FidelityReport(
            route_name=Path(gpx_path).stem,
            backend="local-dem",
            coverage_pct=0.0,
            climbs=[],
            missed_climbs=[],
            verdict="safe",
        )

    lats = parsed["lats"]
    lons = parsed["lons"]
    eles = parsed["eles"]
    dists = parsed["dists"]
    name = parsed["name"]

    climbs = find_climbs(dists, eles)
    verifications: list[ClimbVerification] = []
    fallback_count = 0
    for c in climbs:
        v = _verify_one_climb(c, lats, lons, dists, dem, fallback=fallback)
        if v.fallback_used:
            fallback_count += 1
        verifications.append(v)

    # Single full-route DEM sweep — used for (a) coverage, (b) missed-climb
    # detection, (c) the stitched-profile baseline (replaces GPX altitudes
    # when coverage is high enough). The same sweep used to be discarded
    # after coverage + missed climbs — now it's the baseline for everything.
    presampled = _sample_route_dem(lats, lons, dem, stride_m=25.0, fallback=fallback)
    _, dem_dists, dem_elevs, coverage_pct = presampled

    missed = detect_missed_climbs(
        lats, lons, dists, climbs, dem, fallback=fallback, presampled=presampled,
    )

    deltas = [v.delta_pp for v in verifications]
    verdict = classify_verdict(deltas, missed=len(missed))

    # Pick baseline for the stitched profile: full-route DEM samples when
    # coverage is solid, GPX altitudes otherwise. Climb fine-resamples
    # (5m stride) get laid on top via stitch_profile in either case.
    use_dem_baseline = bool(dem_elevs) and coverage_pct >= DEM_BASELINE_MIN_COVERAGE
    if use_dem_baseline:
        baseline_d, baseline_e = list(dem_dists), list(dem_elevs)
    else:
        # The fallback used to be invisible — only the `backend` string in the
        # Fidelity Report hinted at it. Surface it on stderr so a terminal run
        # sees the warning at run-time, and the report markdown gets a callout
        # via embed_in_prediction so a human reader spots it later.
        if dem_elevs:
            print(
                f"verify_climbs: DEM coverage {coverage_pct:.1f}% < "
                f"{DEM_BASELINE_MIN_COVERAGE:.0f}% threshold — "
                f"baseline elevation falls back to GPX altitudes "
                f"(expect ascent under-count on UK lane terrain).",
                file=sys.stderr,
            )
        else:
            print(
                "verify_climbs: no DEM samples returned for baseline "
                "— falling back to GPX altitudes.",
                file=sys.stderr,
            )
        baseline_d, baseline_e = list(dists), list(eles)

    climb_segments = [
        (v.km_start * 1000.0, v.km_end * 1000.0, v.verified_route_m, v.verified_elevs)
        for v in verifications if v.verified_route_m
    ]
    if climb_segments:
        stitched_d, stitched_e = stitch_profile(
            baseline_d, baseline_e, climb_segments, blend_m=75.0,
        )
    else:
        stitched_d, stitched_e = baseline_d, baseline_e

    backend = "local-dem"
    if use_dem_baseline:
        backend += " (full-route baseline)"
    if fallback_count:
        backend += " + fallback"

    return FidelityReport(
        route_name=name,
        backend=backend,
        coverage_pct=coverage_pct,
        climbs=verifications,
        missed_climbs=missed,
        verdict=verdict,
        stitched_dists=stitched_d,
        stitched_elevs=stitched_e,
    )


VERDICT_LINE = {
    "safe": "Safe to plan — gradients within ±1pp.",
    "minor": "Minor risk — peak gradient understated by up to 2pp.",
    "high": "HIGH RISK — peak gradients underestimated and/or climbs missing.",
}


def stitch_profile(
    gpx_dists: list[float],
    gpx_elevs: list[float],
    climb_segments: list[tuple[float, float, list[float], list[float]]],
    blend_m: float = 75.0,
) -> tuple[list[float], list[float]]:
    """Merge verified climb samples into a GPX-derived profile.

    Petrasova-style: inside each climb window the verified samples replace
    the GPX values entirely; across a `blend_m` zone on each side the two
    sources are linearly weighted (Robinson DSF correction tapered to zero
    at the outer edge of the zone) so the join is continuous.

    Args:
        gpx_dists: cumulative metres along the route, monotonically increasing.
        gpx_elevs: GPX-derived elevations (same length as gpx_dists).
        climb_segments: list of (start_m, end_m, ver_dists, ver_elevs) tuples.
            ver_dists are cumulative metres along the same route as gpx_dists.
        blend_m: width of the blend zone on each side of a climb (metres).

    Returns:
        (stitched_dists, stitched_elevs) — same length, monotonic.
    """
    if not climb_segments:
        return list(gpx_dists), list(gpx_elevs)

    n = len(gpx_dists)
    out_d: list[float] = []
    out_e: list[float] = []
    out_w: list[float] = []  # verified weight per output sample

    climbs = sorted(climb_segments, key=lambda c: c[0])

    gpx_i = 0
    for s_m, e_m, ver_d, ver_e in climbs:
        while gpx_i < n and gpx_dists[gpx_i] < s_m - blend_m:
            out_d.append(gpx_dists[gpx_i])
            out_e.append(gpx_elevs[gpx_i])
            out_w.append(0.0)
            gpx_i += 1
        while gpx_i < n and gpx_dists[gpx_i] < s_m:
            d = gpx_dists[gpx_i]
            w = 0.0 if blend_m <= 0 else max(0.0, min(1.0, (d - (s_m - blend_m)) / blend_m))
            out_d.append(d)
            out_e.append(gpx_elevs[gpx_i])
            out_w.append(w)
            gpx_i += 1
        for d, e in zip(ver_d, ver_e):
            if s_m <= d <= e_m:
                out_d.append(d)
                out_e.append(e)
                out_w.append(1.0)
        while gpx_i < n and gpx_dists[gpx_i] <= e_m:
            gpx_i += 1
        while gpx_i < n and gpx_dists[gpx_i] <= e_m + blend_m:
            d = gpx_dists[gpx_i]
            w = 0.0 if blend_m <= 0 else max(0.0, min(1.0, ((e_m + blend_m) - d) / blend_m))
            out_d.append(d)
            out_e.append(gpx_elevs[gpx_i])
            out_w.append(w)
            gpx_i += 1
    while gpx_i < n:
        out_d.append(gpx_dists[gpx_i])
        out_e.append(gpx_elevs[gpx_i])
        out_w.append(0.0)
        gpx_i += 1

    # Robinson DSF tapered correction in the blend zones.
    if blend_m > 0:
        def _interp(xs, ys, x):
            if x <= xs[0]:
                return ys[0]
            if x >= xs[-1]:
                return ys[-1]
            for j in range(len(xs) - 1):
                if xs[j] <= x <= xs[j + 1]:
                    span = xs[j + 1] - xs[j]
                    if span <= 0:
                        return ys[j]
                    t = (x - xs[j]) / span
                    return ys[j] * (1 - t) + ys[j + 1] * t
            return ys[-1]

        for s_m, e_m, ver_d, ver_e in climbs:
            dH_start = _interp(ver_d, ver_e, s_m) - _interp(gpx_dists, gpx_elevs, s_m)
            dH_end = _interp(ver_d, ver_e, e_m) - _interp(gpx_dists, gpx_elevs, e_m)
            for i, d in enumerate(out_d):
                if s_m - blend_m <= d < s_m:
                    out_e[i] = out_e[i] + dH_start * out_w[i]
                elif e_m < d <= e_m + blend_m:
                    out_e[i] = out_e[i] + dH_end * out_w[i]

    return out_d, out_e


def render_report(report: FidelityReport) -> str:
    lines = ["<!-- BEGIN FIDELITY -->", "## Fidelity Report", ""]
    lines.append(f"**Verdict:** {VERDICT_LINE[report.verdict]}")
    lines.append(f"**Backend:** {report.backend}  ")
    lines.append(f"**Coverage:** {report.coverage_pct:.0f}%  ")
    if any(c.fallback_used for c in report.climbs):
        lines.append("*(Some climbs sampled via GPXZ API fallback.)*  ")
    lines.append("")
    lines.append("### Per-climb comparison")
    lines.append("")
    lines.append("| # | km | GPX peak | Hi-fi peak | Δ | >12% | >10% | >8% |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(report.climbs, start=1):
        if math.isnan(c.verified_peak_pct):
            lines.append(
                f"| {i} | {c.km_start:.2f} | {c.gpx_peak_pct:.1f}% | "
                "(unverified) | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {i} | {c.km_start:.2f} | {c.gpx_peak_pct:.1f}% | "
            f"**{c.verified_peak_pct:.1f}%** | "
            f"{'+' if c.delta_pp >= 0 else ''}{c.delta_pp:.1f}pp | "
            f"{c.length_above_12:.0f}m | "
            f"{c.length_above_10:.0f}m | "
            f"{c.length_above_8:.0f}m |"
        )
    # Mean-max gradient curve — spatial analogue of a power-duration curve.
    # Shows how the climb's gradient demand scales with section length.
    mm_climbs = [c for c in report.climbs if c.mean_max]
    if mm_climbs:
        lines.append("")
        lines.append("### Gradient profile (steepest sustained over window)")
        lines.append("")
        lines.append("| # | km | peak-25m | peak-100m | peak-500m | peak-1km |")
        lines.append("|---|---|---|---|---|---|")
        for i, c in enumerate(report.climbs, start=1):
            mm = c.mean_max
            if not mm:
                continue
            def _f(v):
                return f"{v:.1f}%" if v is not None else "—"
            lines.append(
                f"| {i} | {c.km_start:.2f} | {_f(mm.get('peak_25m'))} | "
                f"{_f(mm.get('peak_100m'))} | {_f(mm.get('peak_500m'))} | "
                f"{_f(mm.get('peak_1km'))} |"
            )

    # Walls — segments >= 10% sustained for >= 30m, with location within climb.
    wall_climbs = [c for c in report.climbs if c.walls]
    if wall_climbs:
        lines.append("")
        lines.append("### Walls (≥10% sustained ≥30m)")
        lines.append("")
        lines.append("| # | km | Offset | Length | Peak | Position |")
        lines.append("|---|---|---|---|---|---|")
        for i, c in enumerate(report.climbs, start=1):
            for w in c.walls:
                lines.append(
                    f"| {i} | {c.km_start:.2f} | "
                    f"+{w['offset_m']:.0f} m | "
                    f"{w['length_m']:.0f} m | "
                    f"**{w['peak_pct']:.1f}%** | "
                    f"{w['pct_in']:.0f}% in |"
                )

    # Hi-fi pacing recompute — uses verified avg/peak instead of GPX.
    pacing_climbs = [c for c in report.climbs if c.verified_pacing]
    if pacing_climbs:
        lines.append("")
        lines.append("### Hi-fi pacing (physics on hi-fidelity gradients)")
        lines.append("")
        lines.append(
            "| # | km | Len (m) | Gain (m) | Avg | Peak | "
            "V@FTP (km/h) | V@MAP (km/h) | V@Z3 (km/h) | "
            "t@FTP (min) | VAM (m/h) | Survive (W) |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(report.climbs, start=1):
            p = c.verified_pacing
            if not p:
                continue
            lines.append(
                f"| {i} | {c.km_start:.2f} | {p['length_m']:.0f} | "
                f"{p['gain_m']:.0f} | {p['avg_pct']:.1f}% | "
                f"**{p['peak_pct']:.1f}%** | "
                f"{p['speed_ftp_kmh']:.1f} | {p['speed_map_kmh']:.1f} | "
                f"{p['speed_z3_kmh']:.1f} | "
                f"{p['duration_ftp_min']:.1f} | "
                f"{p['vam_ftp']:.0f} | "
                f"{p['survival_w']:.0f} |"
            )

    if report.missed_climbs:
        lines.append("")
        lines.append("### Missed climbs (in DEM, not in GPX)")
        lines.append("")
        lines.append("Climbs the GPX altitudes flattened. Hi-fi pass found them and "
                     "re-sampled at 5m stride / 30m smoothing — same accuracy as "
                     "declared climbs.")
        lines.append("")
        lines.append("| km | Coarse peak | Hi-fi peak | Δ |")
        lines.append("|---|---|---|---|")
        for cv in report.missed_climbs:
            # gpx_peak_pct here holds the coarse-pass peak (seeded by
            # detect_missed_climbs from the 100m-window scan).
            lines.append(
                f"| {cv.km_start:.2f} | {cv.gpx_peak_pct:.1f}% | "
                f"**{cv.verified_peak_pct:.1f}%** | "
                f"{'+' if cv.delta_pp >= 0 else ''}{cv.delta_pp:.1f}pp |"
            )
        # Show walls inside any missed climb that has them — the rider's
        # most-actionable signal.
        wall_missed = [cv for cv in report.missed_climbs if cv.walls]
        if wall_missed:
            lines.append("")
            lines.append("**Walls inside missed climbs:**")
            lines.append("")
            lines.append("| km | Offset | Length | Peak | Position |")
            lines.append("|---|---|---|---|---|")
            for cv in wall_missed:
                for w in cv.walls:
                    lines.append(
                        f"| {cv.km_start:.2f} | "
                        f"+{w['offset_m']:.0f} m | "
                        f"{w['length_m']:.0f} m | "
                        f"**{w['peak_pct']:.1f}%** | "
                        f"{w['pct_in']:.0f}% in |"
                    )
    lines.append("")
    lines.append("<!-- END FIDELITY -->")
    return "\n".join(lines) + "\n"


_FIDELITY_BLOCK = re.compile(
    r"<!-- BEGIN FIDELITY -->.*?<!-- END FIDELITY -->\n?",
    re.DOTALL,
)

# Hi-fi pacing in the Fidelity Report supersedes the per-climb GPX pacing
# bullets in the body. Strip those bullets so the document has one source
# of truth. Markers emitted by analyse_gpx.format_markdown.
_GPX_PACING_BLOCK = re.compile(
    r"<!-- BEGIN GPX-PACING -->\n.*?<!-- END GPX-PACING -->\n?",
    re.DOTALL,
)


# Coverage threshold for promoting full-route DEM samples to the stitched
# baseline. Below this, the verifier falls back to GPX altitudes (which under-
# count UK lane terrain — see calibration log 2026-06-13 Lesson 1). Promoted
# to module level so verify_route and embed_in_prediction share the same
# constant and can produce matching stderr + markdown warnings.
DEM_BASELINE_MIN_COVERAGE = 80.0


_ASCENT_LINE = re.compile(
    r"^- \*\*Ascent\*\*: (\d+) m(?: \(hi-fi resampled; GPX: (\d+) m\))?$",
    re.MULTILINE,
)

# Per-climb GPX section in the prediction body. Once a Fidelity Report exists
# with verified pacing, this whole section becomes redundant — the Fidelity
# tables carry length / gain / avg / max plus hi-fi pacing on the same climbs.
# Match from the `## Climbs (N)` header to end-of-document (Climbs is the
# last section format_markdown emits).
_GPX_CLIMBS_SECTION = re.compile(
    r"\n## Climbs \(\d+\)\n.*\Z",
    re.DOTALL,
)

# Coverage-fallback callout, prepended to the doc when DEM coverage was too
# low for a full-route baseline. Matches the stock + already-inserted forms so
# re-running is idempotent.
_COVERAGE_WARNING_BLOCK = re.compile(
    r"<!-- BEGIN COVERAGE-WARN -->.*?<!-- END COVERAGE-WARN -->\n?",
    re.DOTALL,
)


# Markers for the TSS / moving-time block in the Summary section.
# Mirrors what analyse_gpx.format_markdown emits. The optional annotation
# group is permissive (any content that ends with "GPX: ~NNN") so re-running
# stays idempotent across annotation variants ("hi-fi resampled",
# "hi-fi resampled + terrain-adjusted", etc.).
_MOVING_TIME_LINE = re.compile(
    r"^- \*\*Total moving time\*\*: ~([\d.]+) h"
    r"(?: \([^)]*GPX: ~([\d.]+) h\))?$",
    re.MULTILINE,
)
_TSS_IF_LINE = re.compile(
    r"^- \*\*TSS at IF (0\.\d+)\*\* (\([^)]+\)): ~(\d+)"
    r"(?: \([^)]*GPX: ~(\d+)\))?$",
    re.MULTILINE,
)
_WALL_DENSITY_LINE = re.compile(
    r"^- \*\*Wall density\*\*: [^\n]+$",
    re.MULTILINE,
)


def _hifi_total_ascent_m(elevs: list[float], w: int = 9) -> int:
    """Same smooth + positive-diff sum used by analyse_gpx, on stitched elevs."""
    n = len(elevs)
    if n < 2:
        return 0
    ww = min(w, n)
    half = ww // 2
    sm = [0.0] * n
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        sm[i] = sum(elevs[lo:hi]) / (hi - lo)
    return int(round(sum(max(0.0, sm[i] - sm[i - 1]) for i in range(1, n))))


def _wall_density_m_per_km(
    report: "FidelityReport", distance_km: float
) -> float | None:
    """Meters of route ridden at ≥8% grade, divided by route distance (km).

    Sums `length_above_8` across all verified climbs (both `report.climbs`
    and `report.missed_climbs` — missed climbs are still verified, just not
    originally declared by GPX find_climbs). Returns `None` when no verified
    climbs exist (e.g. DEM coverage too low) — caller should skip the
    terrain adjustment in that case rather than guess.

    Why this metric over `ascent_per_km`:
      Richmond Park and Lost Lane #21 both score ~10 m/km in ascent rate,
      but Richmond's "climbs" are 2–4% shallow rolls while Lost Lane #21
      has real 10–12% wall pitches. The IF impact is fundamentally different,
      and the rolling-shallow vs punchy-wall distinction is exactly what
      `length_above_8` captures. Same reading of "0 m/km at ≥8%" for
      Richmond → no terrain lift; "5.9 m/km" for Lost Lane → terrain lift.
    """
    all_climbs = list(report.climbs) + list(report.missed_climbs)
    if not all_climbs:
        return None
    walls_m = sum(getattr(c, "length_above_8", 0.0) for c in all_climbs)
    return walls_m / max(distance_km, 1.0)


def _wall_density_multiplier(wall_density_m_per_km: float) -> float:
    """Lift the predicted IF and moving time when terrain forces surges.

    Calibration anchor (2026-06-13 Lost Lane #21):
      - 5.9 m/km of route at ≥8% grade
      - Route predicted IF 0.65–0.75; actual rode IF 0.80
      - Lift factor ~1.10 captures the terrain-only portion of the surge
        load. The remaining 0.10 in 1.23 ratio is partner factor (Thomas),
        handled separately in USER_PROFILE.md § Riding partners.

    Map:
      0 m/km     → 1.00 (smooth tarmac + shallow rolls — Richmond Park)
      6 m/km     → 1.10 (typical UK lane Saturday — Lost Lane #21)
      12 m/km    → 1.20 (notably wall-dense)
      20+ m/km   → 1.30 (mountainous; capped)

    One data point so far (Lost Lane #21). Curve to be refit when 2–3
    further mixed-terrain rides are logged.
    """
    return min(1.30, 1.0 + 0.017 * max(0.0, wall_density_m_per_km))


def _rewrite_tss_block_from_stitched(
    text: str, report: FidelityReport
) -> str:
    """Re-run analyse_gpx.find_climbs + estimate_tss on the verified hi-fi
    (stitched) elevation profile, then rewrite the moving-time and TSS-at-IF
    lines in the Summary block, preserving GPX values in parens
    (same pattern as `_ASCENT_LINE`). Also annotates the block with a
    wall-density-aware terrain-adjusted IF band.

    Re-running is idempotent: the regexes match both the stock and the
    already-rewritten forms; gating skips no-op replacements.

    Lazy imports keep verify_climbs lightweight at module load. analyse_gpx
    already imports verify_climbs lazily on its own side, so no circular
    import is introduced.
    """
    if not (report.stitched_dists and report.stitched_elevs):
        return text

    import numpy as np
    from analyse_gpx import find_climbs, estimate_tss

    stitched_d = np.asarray(report.stitched_dists, dtype=float)
    stitched_e = np.asarray(report.stitched_elevs, dtype=float)
    if len(stitched_d) < 50 or stitched_d[-1] < 1000:
        return text

    distance_km = float(stitched_d[-1]) / 1000.0
    hifi_climbs = find_climbs(stitched_d, stitched_e)
    hifi_tss = estimate_tss(distance_km, hifi_climbs, target_if=0.65)
    raw_hours = float(hifi_tss["estimated_total_hours"])

    # Wall-density multiplier compresses both intensity (NP surges) and time
    # (slower flats due to corners + descents + narrow lanes). Lost Lane #21
    # (2026-06-13) calibration: predicted 2.59 h / IF 0.65–0.75 / TSS 109–146;
    # actual 3.08 h / IF 0.80 / TSS 193. Hi-fi recompute alone only moved
    # 2.59 → 2.65 h because estimate_tss assumes 25 km/h on flats — but UK
    # lane terrain pushes the effective flat-section speed down. The same
    # wall-density factor that lifts IF should slow time by a similar amount.
    #
    # Metric: meters of route at ≥8% grade per km of route. Differentiates
    # punchy-wall terrain from rolling-shallow terrain at the same overall
    # ascent rate (see _wall_density_m_per_km).
    wall_density = _wall_density_m_per_km(report, distance_km)
    mult = _wall_density_multiplier(wall_density) if wall_density is not None else 1.0
    new_hours = round(raw_hours * mult, 2) if mult > 1.001 else raw_hours
    # Re-derive the IF-band TSS from the adjusted hours so the band, the
    # moving-time line, and the wall-density annotation all stay self-consistent.
    new_tss = {
        "0.65": int(round(new_hours * 0.65 ** 2 * 100)),
        "0.70": int(round(new_hours * 0.70 ** 2 * 100)),
        "0.75": int(round(new_hours * 0.75 ** 2 * 100)),
    }

    annot = (
        "hi-fi resampled + terrain-adjusted"
        if mult > 1.001
        else "hi-fi resampled"
    )

    # 1. Moving-time line
    mtm = _MOVING_TIME_LINE.search(text)
    if mtm:
        gpx_hours = float(mtm.group(2)) if mtm.group(2) else float(mtm.group(1))
        if abs(new_hours - gpx_hours) >= max(0.15, 0.05 * gpx_hours):
            text = _MOVING_TIME_LINE.sub(
                f"- **Total moving time**: ~{new_hours} h "
                f"({annot}; GPX: ~{gpx_hours} h)",
                text, count=1,
            )

    # 2. Three TSS-at-IF lines
    def _replace_tss(m):
        if_val = m.group(1)
        label = m.group(2)
        existing = int(m.group(3))
        gpx_tss = int(m.group(4)) if m.group(4) else existing
        if if_val not in new_tss:
            return m.group(0)
        adj_tss = new_tss[if_val]
        if abs(adj_tss - gpx_tss) < max(5, int(0.05 * gpx_tss)):
            return m.group(0)
        return (
            f"- **TSS at IF {if_val}** {label}: ~{adj_tss} "
            f"({annot}; GPX: ~{gpx_tss})"
        )

    text = _TSS_IF_LINE.sub(_replace_tss, text)

    # 3. Wall-density annotation (single line after the last TSS line).
    if wall_density is None:
        density_line = (
            "- **Wall density**: not measured (no verified climbs in the "
            "Fidelity Report — DEM coverage too low to count walls). "
            "No terrain adjustment applied; treat the TSS band as an "
            "under-bound on hilly terrain."
        )
    elif mult > 1.001:
        if_lo = round(0.65 * mult, 2)
        if_hi = round(0.75 * mult, 2)
        tss_lo = int(round(new_hours * if_lo ** 2 * 100))
        tss_hi = int(round(new_hours * if_hi ** 2 * 100))
        density_line = (
            f"- **Wall density**: {wall_density:.1f} m/km at ≥8% grade "
            f"→ terrain lifts IF ×{mult:.2f} and time ×{mult:.2f}. "
            f"Expected IF {if_lo:.2f}–{if_hi:.2f}, TSS ~{tss_lo}–{tss_hi} "
            f"(add a riding-partner factor on top — see USER_PROFILE.md § Riding partners)."
        )
    else:
        density_line = (
            f"- **Wall density**: {wall_density:.1f} m/km at ≥8% grade "
            "(below threshold — smooth tarmac / shallow rolls; no terrain "
            "adjustment, base hi-fi numbers stand)."
        )

    if _WALL_DENSITY_LINE.search(text):
        text = _WALL_DENSITY_LINE.sub(density_line, text, count=1)
    else:
        last_tss = None
        for m in _TSS_IF_LINE.finditer(text):
            last_tss = m
        if last_tss:
            insert_at = last_tss.end()
            text = text[:insert_at] + "\n" + density_line + text[insert_at:]

    return text


def embed_in_prediction(md_path: Path, report: FidelityReport) -> None:
    block = render_report(report)
    md_path = Path(md_path)
    text = md_path.read_text() if md_path.exists() else ""
    has_verified_pacing = any(c.verified_pacing for c in report.climbs)
    if has_verified_pacing:
        text = _GPX_PACING_BLOCK.sub("", text)
        # Drop the entire `## Climbs (N)` section: the Fidelity Report above
        # now carries length / gain / avg / max + hi-fi pacing for the same
        # climbs. Keeping both creates a visible inconsistency where the body
        # `### Climb` headers still show GPX-derived numbers
        # (handover 2026-06-13 follow-up B).
        text = _GPX_CLIMBS_SECTION.sub("\n", text)
    if _FIDELITY_BLOCK.search(text):
        text = _FIDELITY_BLOCK.sub(block, text)
    else:
        m = re.search(r"^# .+\n", text, re.MULTILINE)
        if m:
            insert_at = m.end()
            text = text[:insert_at] + "\n" + block + "\n" + text[insert_at:]
        else:
            text = block + "\n" + text

    # Coverage-fallback callout. Visible above the Fidelity Report when DEM
    # coverage was too low to use a full-route baseline — flags that the
    # numbers in the prediction may under-count ascent.
    using_dem_baseline = (
        bool(report.stitched_elevs)
        and report.coverage_pct >= DEM_BASELINE_MIN_COVERAGE
    )
    text = _COVERAGE_WARNING_BLOCK.sub("", text)
    if report.stitched_elevs and not using_dem_baseline:
        warn_block = (
            "<!-- BEGIN COVERAGE-WARN -->\n"
            f"> ⚠️ **DEM coverage {report.coverage_pct:.1f}% below "
            f"{DEM_BASELINE_MIN_COVERAGE:.0f}% threshold.** "
            "Baseline elevation falls back to GPX altitudes — expect ascent "
            "and TSS to under-count, especially on UK lane terrain. Consider "
            "re-running after downloading the missing DEM tiles "
            "(`fetch_dem_tiles.py` or the interactive prompt).\n"
            "<!-- END COVERAGE-WARN -->\n"
        )
        m_fid = _FIDELITY_BLOCK.search(text)
        if m_fid:
            insert_at = m_fid.start()
            text = text[:insert_at] + warn_block + "\n" + text[insert_at:]
        else:
            m_h1 = re.search(r"^# .+\n", text, re.MULTILINE)
            if m_h1:
                insert_at = m_h1.end()
                text = text[:insert_at] + "\n" + warn_block + text[insert_at:]

    if report.stitched_elevs:
        hifi_asc = _hifi_total_ascent_m(list(report.stitched_elevs))

        # Rewrite the Ascent headline (existing behaviour).
        m = _ASCENT_LINE.search(text)
        if m:
            gpx_asc = int(m.group(2)) if m.group(2) else int(m.group(1))
            if abs(hifi_asc - gpx_asc) >= max(20, int(0.05 * gpx_asc)):
                replacement = (
                    f"- **Ascent**: {hifi_asc} m "
                    f"(hi-fi resampled; GPX: {gpx_asc} m)"
                )
                text = _ASCENT_LINE.sub(replacement, text, count=1)

        # Rewrite the TSS estimate block from the hi-fi profile,
        # plus a wall-density-aware terrain-adjusted IF annotation
        # (closes the gap between predicted IF 0.65–0.75 and actual
        # IF 0.80 on hilly UK lane routes — see calibration log
        # 2026-06-13 Lesson 3 in the handover).
        text = _rewrite_tss_block_from_stitched(text, report)

    md_path.write_text(text)


def resolve_coverage_policy(flag: str | None, interactive: bool, has_key: bool) -> str:
    if flag is not None:
        return flag
    if interactive:
        return "prompt"
    return "api" if has_key else "skip"


def prompt_coverage_gap(missing_tiles: list[str], total_mb: int) -> str:
    """Interactive prompt; returns 'download' / 'api' / 'skip' / 'quit'."""
    print(f"Route extends outside loaded DEM tiles.")
    print(f"Missing tiles: {', '.join(missing_tiles[:10])}"
          + (f"... ({len(missing_tiles)} total)" if len(missing_tiles) > 10 else ""))
    print(f"Estimated download size: ~{total_mb} MB")
    print()
    print("  [d] Download missing tiles now and verify locally   (recommended)")
    print("  [a] Use GPXZ API for the uncovered segments only")
    print("  [s] Skip verification on uncovered segments and proceed")
    print("  [q] Quit")
    while True:
        choice = input("Your choice [d]: ").strip().lower() or "d"
        if choice in ("d", "download"): return "download"
        if choice in ("a", "api"): return "api"
        if choice in ("s", "skip"): return "skip"
        if choice in ("q", "quit"): return "quit"
        print("Please answer d / a / s / q.")
