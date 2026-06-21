"""Robustness + math tests for compare_riders pure helpers."""
import numpy as np

import compare_riders as cr


def test_detect_flat_attacks_no_altitude_returns_empty():
    # Power-only / no-baro FIT: altitude key absent → no crash, empty result.
    arr = {"distance_m": np.arange(200, dtype=float), "power_w": np.full(200, 300.0)}
    assert cr.detect_flat_attacks(arr, ftp_w=250) == []


def test_detect_flat_attacks_zero_ftp_returns_empty():
    arr = {
        "distance_m": np.arange(200, dtype=float) * 10,
        "altitude_m": np.zeros(200),
        "power_w": np.full(200, 300.0),
    }
    # ftp_w=0 must NOT flag every sample as a surge.
    assert cr.detect_flat_attacks(arr, ftp_w=0) == []


def test_detect_flat_attacks_finds_a_surge():
    n = 300
    arr = {
        "distance_m": np.arange(n, dtype=float) * 10,   # flat-ish spacing
        "altitude_m": np.zeros(n),                       # perfectly flat
        "power_w": np.concatenate([np.full(100, 150.0),
                                    np.full(60, 400.0),  # 60 s surge
                                    np.full(140, 150.0)]),
    }
    attacks = cr.detect_flat_attacks(arr, ftp_w=250, threshold_pct=120)
    assert len(attacks) == 1
    assert attacks[0]["duration_s"] == 60
    assert attacks[0]["peak_w"] == 400.0


def test_efficiency_factor_zero_hr_guarded():
    assert cr.efficiency_factor(250, 0) == 0.0


def test_normalized_power_constant_is_itself():
    assert abs(cr.normalized_power(np.full(120, 200.0)) - 200.0) < 1e-6


def test_time_in_zones_zero_ftp_empty():
    assert cr.time_in_zones(np.full(100, 200.0), ftp=0) == {}
