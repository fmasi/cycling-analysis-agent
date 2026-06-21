"""Tests for the shared power_metrics module."""
import numpy as np

import power_metrics as pm


def test_peak_power_curve_captures_start_window():
    # Regression: the old analyse_fit cumsum implementation was off-by-one and
    # dropped the window starting at index 0 — a peak at the very start was
    # under-reported. Convolve captures it.
    arr = np.array([300.0] * 5 + [50.0] * 20)
    assert pm.peak_power_curve(arr, [5], missing="omit")[5] == 300.0


def test_peak_power_curve_missing_contracts():
    arr = np.arange(10, dtype=float)
    # window longer than the series
    assert pm.peak_power_curve(arr, [50], missing="none")[50] is None     # key kept, None
    assert 50 not in pm.peak_power_curve(arr, [50], missing="omit")       # key omitted


def test_peak_power_curve_w1_is_instantaneous_max():
    arr = np.array([10.0, 99.0, 20.0])
    assert pm.peak_power_curve(arr, [1])[1] == 99.0


def test_peak_power_curve_handles_nan():
    arr = np.array([100.0, np.nan, 100.0, 100.0])
    # NaN treated as 0 → a 2-window peak is the [100,100] pair = 100
    assert pm.peak_power_curve(arr, [2])[2] == 100.0


def test_normalized_power_constant_and_short():
    assert abs(pm.normalized_power(np.full(120, 200.0)) - 200.0) < 1e-6
    assert pm.normalized_power(np.array([])) == 0.0
    assert abs(pm.normalized_power(np.full(10, 150.0)) - 150.0) < 1e-6   # <30 → mean


def test_variability_and_efficiency_guards():
    assert pm.variability_index(250, 0) == 0.0
    assert abs(pm.variability_index(260, 200) - 1.3) < 1e-9
    assert pm.efficiency_factor(250, 0) == 0.0
    assert abs(pm.efficiency_factor(180, 150) - 1.2) < 1e-9


def test_time_in_zones_guards_and_sum():
    assert pm.time_in_zones(np.array([]), 250) == {}
    assert pm.time_in_zones(np.full(100, 200.0), 0) == {}
    z = pm.time_in_zones(np.full(100, 0.80 * 250), 250)   # all in Z3 (75–90%)
    assert z["Z3 (75–90%)"] == 100.0
    assert abs(sum(z.values()) - 100.0) < 0.1


def test_peak_speed_curve_matches_rolling():
    s = np.array([10.0, 30.0, 30.0, 10.0])
    assert pm.peak_speed_curve(s, [2])[2] == 30.0
