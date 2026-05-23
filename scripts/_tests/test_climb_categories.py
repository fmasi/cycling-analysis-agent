import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from climb_categories import categorise, is_significant, select_climbs_for_detail


class FakeVer:
    def __init__(self, peak_25m=None, walls=None):
        self.mean_max = {"peak_25m": peak_25m}
        self.walls = walls or []


def climb(length_m, avg, mx=0.0):
    return {"length_m": length_m, "avg_grad_pct": avg, "max_grad_pct": mx,
            "start_km": 0.0, "end_km": length_m / 1000.0}


def test_categorise_cat3_threshold():
    name, pts, _b, _f, index = categorise(2.0, 4.0)
    assert name == "Cat 3"
    assert abs(index - 8.0) < 1e-6


def test_significant_cat3_by_index():
    ok, reason = is_significant(climb(2000, 4.0))
    assert ok and "Cat 3" in reason


def test_significant_short_steep_by_peak25():
    ok, reason = is_significant(climb(580, 4.9), FakeVer(peak_25m=10.6))
    assert ok and "peak-25m" in reason


def test_significant_by_wall():
    ok, reason = is_significant(climb(580, 4.9),
                                FakeVer(peak_25m=6.0, walls=[{"length_m": 40}]))
    assert ok and "wall" in reason


def test_not_significant_gentle_drag():
    ok, _ = is_significant(climb(1100, 2.2, mx=4.2), FakeVer(peak_25m=4.4))
    assert not ok


def test_significant_lofi_fallback_uses_gpx_max():
    ok, reason = is_significant(climb(580, 4.9, mx=9.9), verification=None)
    assert ok and "GPX" in reason


def test_select_cat3_never_capped():
    climbs = [climb(2000, 4.0)] * 3
    idx = select_climbs_for_detail(climbs, mode="auto", cap=1)
    assert idx == [0, 1, 2]


def test_select_caps_minor_climbs():
    climbs = [climb(2000, 4.0), climb(300, 5.0), climb(300, 5.0), climb(300, 5.0)]
    vers = [FakeVer(), FakeVer(peak_25m=12.0), FakeVer(peak_25m=10.0),
            FakeVer(peak_25m=9.0)]
    idx = select_climbs_for_detail(climbs, vers, mode="auto", cap=2)
    assert idx == [0, 1, 2]


def test_select_mode_all_and_none():
    climbs = [climb(2000, 4.0), climb(300, 2.0)]
    assert select_climbs_for_detail(climbs, mode="all") == [0, 1]
    assert select_climbs_for_detail(climbs, mode="none") == []


def test_select_mode_explicit_indices():
    climbs = [climb(2000, 4.0), climb(300, 2.0), climb(400, 3.0)]
    assert select_climbs_for_detail(climbs, mode=[1, 3]) == [0, 2]
