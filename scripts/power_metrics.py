"""Shared power/speed metrics: peak-power curve, NP, VI, time-in-zones, EF.

Single home for the rolling-window + intensity maths that analyse_fit and
compare_riders both rely on (previously the peak-power curve was implemented
twice, with subtly different edge contracts). All functions are pure numpy.
"""
from __future__ import annotations

import numpy as np


def rolling_peak(series, windows_s, *, missing: str = "none") -> dict:
    """Best rolling-average value at each window length (seconds).

    Uniform rolling mean (a 1 s window is the instantaneous max). NaNs are
    treated as 0. `missing` controls windows longer than the series:
      - "none": map the window to None (key kept) — compare_riders' contract.
      - "omit": leave the window out of the result — analyse_fit's contract.
    """
    p = np.nan_to_num(np.asarray(series, dtype=float), nan=0.0)
    out: dict = {}
    for w in windows_s:
        if w <= 0:
            continue
        if len(p) < w:
            if missing == "none":
                out[w] = None
            continue
        c = np.convolve(p, np.ones(w) / w, mode="valid")
        out[w] = float(c.max())
    return out


def peak_power_curve(power, windows_s, *, missing: str = "none") -> dict:
    """Best rolling-average power (W) at each window length (seconds)."""
    return rolling_peak(power, windows_s, missing=missing)


def peak_speed_curve(speed_kmh, windows_s, *, missing: str = "none") -> dict:
    """Best rolling-average speed (km/h) at each window length (seconds)."""
    return rolling_peak(speed_kmh, windows_s, missing=missing)


def normalized_power(power) -> float:
    """NP: 30 s rolling avg → 4th power → mean → 4th root."""
    p = np.nan_to_num(np.asarray(power, dtype=float), nan=0.0)
    if len(p) < 30:
        return float(p.mean()) if len(p) else 0.0
    roll = np.convolve(p, np.ones(30) / 30, mode="valid")
    return float((np.mean(roll ** 4)) ** 0.25)


def variability_index(np_w: float, avg_w: float) -> float:
    """VI = NP / avg power. >1.05 means a punchy/variable ride."""
    return np_w / avg_w if avg_w > 0 else 0.0


def efficiency_factor(np_w: float, avg_hr: float) -> float:
    """EF = NP / avg HR. Aerobic fitness proxy; higher is more efficient."""
    return np_w / avg_hr if avg_hr > 0 else 0.0


def time_in_zones(power, ftp: float) -> dict:
    """Time-in-zones (Coggan), as percentage of total non-zero samples."""
    if power is None or len(power) == 0 or ftp <= 0:
        return {}
    p = np.asarray(power, dtype=float)
    p = p[p > 0]
    if len(p) == 0:
        return {}
    bins = {
        "Z1 (<55%)":       (0,          0.55 * ftp),
        "Z2 (55–75%)":     (0.55 * ftp, 0.75 * ftp),
        "Z3 (75–90%)":     (0.75 * ftp, 0.90 * ftp),
        "Z4 (90–105%)":    (0.90 * ftp, 1.05 * ftp),
        "Z5 (105–120%)":   (1.05 * ftp, 1.20 * ftp),
        "Z6 (120–150%)":   (1.20 * ftp, 1.50 * ftp),
        "Z7 (>150%)":      (1.50 * ftp, float("inf")),
    }
    total = len(p)
    out = {}
    for name, (lo, hi) in bins.items():
        n = np.sum((p > lo) & (p <= hi))
        out[name] = round(100.0 * n / total, 1)
    return out
