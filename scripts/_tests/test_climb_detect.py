"""Characterization + unit tests for the shared climb_detect module."""
import numpy as np

import climb_detect as cd


def _ramp_route():
    # 8 km route with one 1.5 km @ 7% climb starting at 3 km, mild noise.
    d = np.arange(0, 8000, 5.0)
    e = np.zeros_like(d)
    for i, x in enumerate(d):
        if 3000 <= x <= 4500:
            e[i] = (x - 3000) * 0.07
        elif x > 4500:
            e[i] = 1500 * 0.07
    e += np.sin(d / 50.0) * 0.2   # deterministic ripple (no RNG)
    return d, e


def test_find_climbs_detects_the_ramp():
    d, e = _ramp_route()
    climbs = cd.find_climbs(d, e)
    assert len(climbs) == 1
    c = climbs[0]
    assert 2.5 < c['start_km'] < 3.5
    assert 6.0 < c['avg_grad_pct'] < 8.0
    # end-of-segment gain/length consistency
    assert abs(c['avg_grad_pct'] - c['gain_m'] / c['length_m'] * 100) < 1e-6


def test_find_climbs_flat_route_is_empty():
    d = np.arange(0, 5000, 5.0)
    e = np.zeros_like(d)
    assert cd.find_climbs(d, e) == []


def test_find_climbs_accepts_python_lists():
    d, e = _ramp_route()
    assert cd.find_climbs(list(d), list(e)) == cd.find_climbs(d, e)


def test_compute_max_grade_catches_a_wall():
    # 50 m @ 12% wall embedded in a 4% background over a 1 km segment.
    d = np.arange(0, 1000, 5.0)
    e = 0.04 * d
    wall = (d >= 500) & (d <= 550)
    e[wall] += (d[wall] - 500) * (0.12 - 0.04)
    mg = cd.compute_max_grade(d, e, 0, 1000)
    assert mg > 8.0


def test_median_filter_kills_single_spike():
    arr = np.array([10.0, 10.0, 99.0, 10.0, 10.0])
    out = cd.median_filter_1d(arr, size=3)
    assert out[2] == 10.0   # spike removed
