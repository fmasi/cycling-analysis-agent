import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from chart_climb_detail import plot_climb_detail


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


def test_plot_climb_detail_too_short_returns_false(tmp_path):
    d = np.array([0.0, 10.0])
    alt = np.array([20.0, 21.0])
    arrays = {"distance_m": d, "altitude_m": alt}
    climb = {"start_km": 0.0, "end_km": 0.01, "length_m": 10.0,
             "avg_grad_pct": 10.0, "max_grad_pct": 10.0}
    out = tmp_path / "climb_short.png"
    assert plot_climb_detail(arrays, climb, 1, out) is False
