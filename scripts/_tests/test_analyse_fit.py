"""Known-answer tests for analyse_fit's HR zones (Coggan, % of LTHR; CLAUDE.md "HR zone formula").

At LTHR 172 the zone floors are round(172 x 0.69 / 0.84 / 0.95 / 1.06) = 119 / 144 / 163 / 182 bpm,
i.e. Z1 <119, Z2 119-143, Z3 144-162, Z4 163-181, Z5 182+ (the bpm set in intervals.icu and on
the rider's devices).
"""
import numpy as np

import analyse_fit as af


def test_zone_bounds_at_lthr_172():
    assert af.hr_zone_bounds(172) == [
        ('Z1 Recovery', 0, 119), ('Z2 Endurance', 119, 144), ('Z3 Tempo', 144, 163),
        ('Z4 Threshold', 163, 182), ('Z5 VO2max', 182, None)]


def test_time_in_zone_counts_each_edge_correctly():
    hr = np.array([0, 90, 118, 119, 143, 144, 162, 163, 181, 182, 200], dtype=float)
    assert af.hr_zones_distribution(hr, lthr=172) == {
        'Z1 Recovery': 2,     # 90, 118 (0 = no reading, excluded)
        'Z2 Endurance': 2,    # 119, 143
        'Z3 Tempo': 2,        # 144, 162
        'Z4 Threshold': 2,    # 163, 181
        'Z5 VO2max': 2,       # 182, 200
    }


def test_no_hr_returns_empty():
    assert af.hr_zones_distribution(np.zeros(5), lthr=172) == {}
