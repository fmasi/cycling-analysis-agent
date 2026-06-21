"""Tests for analyse_gpx pure helpers: estimate_tss and predict_climb."""
import analyse_gpx as ag


def test_estimate_tss_flat_only():
    r = ag.estimate_tss(distance_km=50, climbs=[])
    assert r['estimated_total_hours'] == 2.0          # 50 km / 25 km/h
    assert r['estimated_tss_at_if_065'] == 85          # round(2 * 0.65^2 * 100)


def test_estimate_tss_emits_uncertainty_band():
    r = ag.estimate_tss(distance_km=50, climbs=[])
    lo, hi = r['tss_range']
    assert lo < r['estimated_tss_at_if_065']        # band low below the easy point
    assert hi > r['estimated_tss_at_if_075']        # band high above the firm point
    h_lo, h_hi = r['hours_range']
    assert h_lo < r['estimated_total_hours'] < h_hi  # time band brackets the central estimate


def test_estimate_tss_clamps_negative_flat():
    # Climb lengths summing beyond the route distance must not drive flat_km
    # negative (which would understate hours/TSS).
    climbs = [
        {'length_m': 40000, 'avg_grad_pct': 6.0},
        {'length_m': 40000, 'avg_grad_pct': 6.0},
    ]
    r = ag.estimate_tss(distance_km=50, climbs=climbs)
    assert r['estimated_total_hours'] > 0
    assert r['estimated_tss_at_if_065'] >= 0


def test_predict_climb_keys_are_profile_independent():
    climb = {'avg_grad_pct': 6.0, 'max_grad_pct': 9.0, 'length_m': 2000}
    out = ag.predict_climb(climb)
    # Stable keys regardless of the rider's current FTP/MAP.
    assert set(out['powers']) == {'ftp', 'map', 'z3', 'z2'}
    ftp = out['powers']['ftp']
    assert ftp['w'] == ag.FTP                          # power == profile FTP
    assert f"{ag.FTP}W" in ftp['label']                # label shows live FTP
    assert ftp['speed_kmh'] > 0 and ftp['time_min'] > 0
    assert 'recommended_intent' in out


def test_is_loop_uses_metric_distance():
    import numpy as np
    # Start == end → loop.
    assert ag._is_loop(np.array([51.5, 51.6, 51.5]), np.array([-0.1, 0.0, -0.1]))
    # Start/end ~13 km apart → not a loop (would've been a false "loop" if the
    # 0.001° longitude test were used at this latitude on tiny deltas).
    assert not ag._is_loop(np.array([51.5, 51.6]), np.array([-0.1, 0.0]))


def test_predict_climb_steeper_is_slower():
    shallow = ag.predict_climb({'avg_grad_pct': 3.0, 'max_grad_pct': 4.0, 'length_m': 2000})
    steep = ag.predict_climb({'avg_grad_pct': 10.0, 'max_grad_pct': 12.0, 'length_m': 2000})
    assert steep['powers']['ftp']['speed_kmh'] < shallow['powers']['ftp']['speed_kmh']
