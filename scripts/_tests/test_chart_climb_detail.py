import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from chart_climb_detail import plot_climb_detail, climb_stats
from power_metrics import normalized_power


def test_climb_stats_np_is_proper_30s_rolling():
    d = np.arange(120, dtype=float) * 5
    p = np.concatenate([np.full(60, 100.0), np.full(60, 300.0)])  # variable effort
    stats = climb_stats({"distance_m": d, "power_w": p}, 0.0, 0.6)
    assert abs(stats['np_w'] - normalized_power(p)) < 1e-6   # proper NP, not 4th-power mean
    assert stats['np_w'] > 200.0                             # NP > avg for variable power


def test_plot_climb_detail_writes_png(tmp_path):
    # 600m climb: flat 0-200m, then ~10% to the top.
    d = np.arange(0, 600, 10, dtype=float)
    alt = np.where(d < 200, 20.0, 20.0 + (d - 200) * 0.10)
    arrays = {"distance_m": d, "altitude_m": alt}
    climb = {"start_km": 0.0, "end_km": 0.6, "length_m": 600.0,
             "avg_grad_pct": 6.6, "max_grad_pct": 10.0}
    out = tmp_path / "climb1.png"
    ok = plot_climb_detail(arrays, climb, 1, out)
    assert ok is True
    assert out.exists() and out.stat().st_size > 1000


def test_per_climb_detail_selection_and_render(tmp_path):
    # Mirrors analyse_gpx --save: select significant climbs, then render each.
    from climb_categories import select_climbs_for_detail
    d = np.arange(0, 2000, 5.0)
    e = np.where(d < 200, 50.0, 50.0 + (d - 200) * 0.07)   # 7% → Cat 3
    climbs = [{"start_km": 0.0, "end_km": 2.0, "length_m": 2000.0,
               "avg_grad_pct": 7.0, "max_grad_pct": 9.0}]
    sel = select_climbs_for_detail(climbs, mode="auto", cap=8)
    assert sel == [0]                                      # Cat 3+ selected
    out = tmp_path / "climb1.png"
    assert plot_climb_detail({"distance_m": d, "altitude_m": e}, climbs[0], 1, out)
    assert out.exists()


def test_plot_climb_detail_too_short_returns_false(tmp_path):
    d = np.array([0.0, 10.0])
    alt = np.array([20.0, 21.0])
    arrays = {"distance_m": d, "altitude_m": alt}
    climb = {"start_km": 0.0, "end_km": 0.01, "length_m": 10.0,
             "avg_grad_pct": 10.0, "max_grad_pct": 10.0}
    out = tmp_path / "climb_short.png"
    assert plot_climb_detail(arrays, climb, 1, out) is False
