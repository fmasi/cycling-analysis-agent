"""Shared climb-detection + elevation-smoothing primitives.

Single source of truth for the climb-finding logic that analyse_fit,
analyse_gpx, analyse_climbs and the chart/verifier modules all rely on. These
functions were previously copy-pasted (with subtle divergences) across
analyse_fit.py and analyse_gpx.py; they now live here and are imported (and
re-exported) by both, so a fix lands everywhere at once.

All functions are pure (numpy in, numpy/list out) and take explicit
(distance_m, altitude_m) arrays — no profile or IO dependency — which makes
them directly unit-testable.
"""
from __future__ import annotations

import numpy as np


def median_filter_1d(arr, size=5):
    """Simple 1-D median filter — removes single-point GPS elevation spikes."""
    arr = np.asarray(arr, dtype=float)
    if size < 2 or len(arr) < size:
        return np.array(arr, dtype=float)
    half = size // 2
    n = len(arr)
    out = np.empty(n, dtype=float)
    for i in range(n):
        s = max(0, i - half)
        e = min(n, i + half + 1)
        out[i] = np.median(arr[s:e])
    return out


def smooth(arr, w=15):
    """Rolling-mean smoother (same-length output)."""
    arr = np.asarray(arr, dtype=float)
    if len(arr) < w:
        return arr.copy()
    return np.convolve(arr, np.ones(w) / w, mode="same")


def compute_max_grade(distance_m, altitude_m, start_m, end_m,
                      win_m=50, median_size=5, step_m=10):
    """
    Robust max-grade estimate over a [start_m, end_m] segment.

    Uses median-filtered raw elevation (kills GPS spikes) + a 50 m grade window
    on a 10 m grid. 50 m ≈ 15 s on the climb at FTP — short enough to capture
    real ramps you'd feel in your legs, long enough to dodge single-point noise.

    NOTE: do NOT use `session.max_pos_grade` from a Wahoo FIT as a substitute.
    That field is the max of the head unit's per-sample (already smoothed)
    grade reading — on 12 May 2026 it reported 5.61% on a climb where this
    function (run over the same records) found 11.3%, agreeing with the hi-fi
    DEM verifier to 0.2pp. The session field is over-smoothed and unsafe for
    peak-grade work.
    """
    distance_m = np.asarray(distance_m, dtype=float)
    altitude_m = np.asarray(altitude_m, dtype=float)
    pad = max(win_m, 100)
    mask = (distance_m >= start_m - pad) & (distance_m <= end_m + pad)
    if mask.sum() < 5:
        return 0.0
    e_filt = median_filter_1d(altitude_m[mask], size=median_size)
    d_in = distance_m[mask]
    grid = np.arange(d_in[0], d_in[-1], step_m)
    if len(grid) < 4:
        return 0.0
    eg = np.interp(grid, d_in, e_filt)
    half = max(1, int(win_m / step_m / 2))
    if len(eg) <= 2 * half:
        return 0.0
    grad = np.zeros_like(eg)
    grad[half:-half] = (eg[2 * half:] - eg[:-2 * half]) / (2 * half * step_m) * 100
    in_seg = (grid >= start_m) & (grid <= end_m)
    if not in_seg.any():
        return 0.0
    return float(grad[in_seg].max())


def find_climbs(distance_m, altitude_m, min_length_m=300, min_gain_m=20,
                min_grade=0.015):
    """
    Identify sustained climbs along a ride/route.

    Detection uses a 200 m rolling gradient on smoothed altitude (appropriate
    for "what counts as a sustained climb"). max_grad_pct is then recomputed
    with a 50 m window on median-filtered raw elevation to give a true max that
    matches what you'd feel on the road, without GPS-noise inflation.

    Returns a list of dicts: start_km, end_km, length_m, gain_m, avg_grad_pct,
    max_grad_pct.
    """
    distance_m = np.asarray(distance_m, dtype=float)
    altitude_m = np.asarray(altitude_m, dtype=float)
    if len(distance_m) < 50:
        return []

    # Smooth altitude (climb DETECTION only — not used for max grade).
    win = min(15, len(altitude_m))
    alt_s = np.convolve(altitude_m, np.ones(win) / win, mode="same")

    max_d = distance_m[-1]
    if max_d < 100:
        return []

    d_grid = np.arange(0, max_d, 50)
    alt_d = np.interp(d_grid, distance_m, alt_s)

    window_n = 4  # 200 m at 50 m grid — climb DETECTION window
    if len(alt_d) <= window_n:
        return []
    grad = (alt_d[window_n:] - alt_d[:-window_n]) / 200
    grad = np.concatenate([np.zeros(window_n // 2), grad,
                           np.zeros(window_n - window_n // 2)])

    in_climb = grad > min_grade
    climbs = []
    i = 0
    while i < len(in_climb):
        if in_climb[i]:
            start = i
            j = i
            while j < len(in_climb) and (in_climb[j] or
                  (j + 4 < len(in_climb) and in_climb[j:j + 4].any())):
                j += 1
            length_m = (j - start) * 50
            gain = alt_d[min(j, len(alt_d) - 1)] - alt_d[start]
            if length_m >= min_length_m and gain >= min_gain_m:
                start_m = float(d_grid[start])
                end_m = float(d_grid[min(j, len(d_grid) - 1)])
                max_grad = compute_max_grade(distance_m, altitude_m, start_m, end_m)
                climbs.append({
                    'start_km': start_m / 1000,
                    'end_km': end_m / 1000,
                    'length_m': float(length_m),
                    'gain_m': float(gain),
                    'avg_grad_pct': float(gain / length_m * 100),
                    'max_grad_pct': max_grad,
                })
            i = j
        else:
            i += 1
    return climbs
