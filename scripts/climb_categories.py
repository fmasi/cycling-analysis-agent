"""Climb categorisation (UCI-style) + significance gate + detail selection.

Shared by analyse_climbs.py (FIT) and analyse_gpx.py (GPX). Categorisation
moved here verbatim from analyse_climbs.py so both tools agree.
"""
from typing import Optional

# (lower_index_inclusive, name, points, badge_colour, fill_colour)
# Order matters: iterate top-down, return on first match.
CATEGORIES = [
    (80, 'HC',    20, '#000000', '#440000'),
    (40, 'Cat 1', 10, '#cc4400', '#ff7700'),
    (16, 'Cat 2',  5, '#cc9900', '#ffcc33'),
    ( 6, 'Cat 3',  2, '#0066cc', '#3399ff'),
    ( 2, 'Cat 4',  1, '#006633', '#33aa66'),
    ( 0, 'uncat',  0, '#888888', '#cccccc'),
]


def categorise(length_km, avg_grade_pct):
    """Return (category_name, kom_points, badge_colour, fill_colour, index)."""
    index = length_km * avg_grade_pct
    for threshold, name, points, badge, fill in CATEGORIES:
        if index >= threshold:
            return name, points, badge, fill, index
    return 'uncat', 0, '#888888', '#cccccc', index


STEEP_PEAK25_PCT = 8.0  # short-pitch gate threshold


def _peak25(verification, climb):
    """Best available peak-25m grade for ranking; falls back to GPX max."""
    if verification is not None:
        mm = getattr(verification, "mean_max", None) or {}
        p = mm.get("peak_25m")
        if p is not None:
            return p
    return float(climb.get("max_grad_pct", 0.0))


def is_significant(climb, verification=None):
    """Return (bool, reason). Cat 3+ OR wall OR steep short pitch (peak-25m
    >= 8%). Without verification, fall back to Cat 3+ or GPX max_grad_pct >= 8%."""
    _n, _p, _b, _f, index = categorise(climb["length_m"] / 1000.0,
                                       climb["avg_grad_pct"])
    if index >= 6:
        return True, f"Cat 3+ (index {index:.1f})"
    if verification is not None:
        if getattr(verification, "walls", None):
            return True, "wall >=10% sustained >=30m"
        mm = getattr(verification, "mean_max", None) or {}
        p25 = mm.get("peak_25m")
        if p25 is not None and p25 >= STEEP_PEAK25_PCT:
            return True, f"steep pitch (peak-25m {p25:.1f}%)"
        return False, ""
    if float(climb.get("max_grad_pct", 0.0)) >= STEEP_PEAK25_PCT:
        return True, f"steep pitch (GPX max {climb['max_grad_pct']:.1f}%)"
    return False, ""


def select_climbs_for_detail(climbs, verifications=None, mode="auto", cap=8):
    """Return sorted list of 0-based climb indices to render detail for.

    mode: 'auto' (gate + cap), 'all', 'none', or a list of 1-based indices.
    Cat 3+ climbs are NEVER dropped by the cap; the cap bounds only the
    sub-Cat-3 climbs that qualified via wall / peak-25m, keeping the hardest.
    """
    n = len(climbs)
    if mode == "none":
        return []
    if mode == "all":
        return list(range(n))
    if isinstance(mode, (list, tuple)):
        return sorted(i - 1 for i in mode if 1 <= i <= n)

    vers = list(verifications) if verifications else [None] * n
    if len(vers) < n:
        vers += [None] * (n - len(vers))

    cat3 = []
    minor = []  # (peak25, index_in_climbs)
    for i, c in enumerate(climbs):
        ok, _reason = is_significant(c, vers[i])
        if not ok:
            continue
        _n, _p, _b, _f, index = categorise(c["length_m"] / 1000.0,
                                           c["avg_grad_pct"])
        if index >= 6:
            cat3.append(i)
        else:
            minor.append((_peak25(vers[i], c), i))

    minor.sort(key=lambda t: t[0], reverse=True)
    minor_idx = [i for _p25, i in minor[:max(0, cap)]]
    return sorted(cat3 + minor_idx)
