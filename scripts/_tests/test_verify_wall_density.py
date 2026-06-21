"""Tests for the (previously untested) wall-density + TSS-rewrite machinery
in verify_climbs: _hifi_total_ascent_m, _wall_density_m_per_km,
_wall_density_multiplier, and _rewrite_tss_block_from_stitched (idempotency).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_climbs import (
    ClimbVerification,
    FidelityReport,
    embed_in_prediction,
    _hifi_total_ascent_m,
    _wall_density_m_per_km,
    _wall_density_multiplier,
    _rewrite_tss_block_from_stitched,
)


def _climb(length_above_8=0.0):
    return ClimbVerification(
        name="c", km_start=1.0, km_end=2.0,
        gpx_peak_pct=9.0, verified_peak_pct=11.0, delta_pp=2.0,
        length_above_8=length_above_8, length_above_10=0.0,
        length_above_12=0.0, length_above_14=0.0, fallback_used=False,
    )


# --- _hifi_total_ascent_m ---------------------------------------------------

def test_ascent_empty_or_single():
    assert _hifi_total_ascent_m([]) == 0
    assert _hifi_total_ascent_m([42.0]) == 0


def test_ascent_monotonic_ramp():
    elevs = [float(x) for x in range(0, 100)]   # +99 over the span
    asc = _hifi_total_ascent_m(elevs)
    # smoothing pulls the ends in slightly; should be just under the raw gain
    assert 90 <= asc <= 99


def test_ascent_flat_is_zero():
    assert _hifi_total_ascent_m([100.0] * 50) == 0


def test_ascent_counts_only_the_ups():
    # Down 50 then up 80 → only the climb counts (~80, minus smoothing).
    elevs = [float(x) for x in range(100, 50, -1)] + [float(x) for x in range(50, 130)]
    asc = _hifi_total_ascent_m(elevs)
    assert 70 <= asc <= 80


# --- _wall_density_m_per_km -------------------------------------------------

def _report(climbs=(), missed=(), **kw):
    return FidelityReport(
        route_name="t", backend="local-dem", coverage_pct=kw.get("coverage", 100.0),
        climbs=list(climbs), missed_climbs=list(missed), verdict="safe",
        stitched_dists=kw.get("dists", []), stitched_elevs=kw.get("elevs", []),
    )


def test_wall_density_none_without_climbs():
    assert _wall_density_m_per_km(_report(), distance_km=30.0) is None


def test_wall_density_sums_declared_and_missed():
    rep = _report(climbs=[_climb(120.0)], missed=[_climb(60.0)])
    # (120 + 60) m of wall over 30 km = 6.0 m/km
    assert _wall_density_m_per_km(rep, distance_km=30.0) == 6.0


def test_wall_density_distance_floored_at_1km():
    rep = _report(climbs=[_climb(50.0)])
    # distance < 1 km is floored to 1 so density can't blow up
    assert _wall_density_m_per_km(rep, distance_km=0.2) == 50.0


# --- _wall_density_multiplier -----------------------------------------------

def test_multiplier_curve_anchors():
    assert _wall_density_multiplier(0.0) == 1.0
    assert abs(_wall_density_multiplier(6.0) - 1.102) < 1e-9     # Lost Lane #21
    assert abs(_wall_density_multiplier(12.0) - 1.204) < 1e-9
    assert _wall_density_multiplier(20.0) == 1.30                # capped
    assert _wall_density_multiplier(100.0) == 1.30              # stays capped
    assert _wall_density_multiplier(-5.0) == 1.0                # clamped at 0


# --- _rewrite_tss_block_from_stitched (idempotency) -------------------------

def _stitched_route():
    # 30 km, 5 m spacing; a 2 km @ 7% climb at km 10 so find_climbs detects it.
    dists, elevs = [], []
    x = 0.0
    while x <= 30000.0:
        dists.append(x)
        if 10000.0 <= x <= 12000.0:
            elevs.append(100.0 + (x - 10000.0) * 0.07)
        elif x > 12000.0:
            elevs.append(100.0 + 140.0)
        else:
            elevs.append(100.0)
        x += 5.0
    return dists, elevs


STOCK_SUMMARY = (
    "## Summary\n"
    "- **Total moving time**: ~0.5 h\n"
    "- **TSS at IF 0.65** (easy social): ~21\n"
    "- **TSS at IF 0.70** (moderate endurance): ~25\n"
    "- **TSS at IF 0.75** (firm endurance): ~28\n"
)


def test_rewrite_engages_then_is_idempotent():
    dists, elevs = _stitched_route()
    rep = _report(climbs=[_climb(150.0)], dists=dists, elevs=elevs)

    once = _rewrite_tss_block_from_stitched(STOCK_SUMMARY, rep)
    twice = _rewrite_tss_block_from_stitched(once, rep)

    assert once != STOCK_SUMMARY                 # it actually rewrote
    assert "GPX: ~0.5 h" in once                 # original value preserved in parens
    assert "terrain-adjusted" in once            # wall density > 0 → terrain lift
    assert twice == once                         # second pass is a no-op


def test_rewrite_no_stitched_profile_is_noop():
    rep = _report(climbs=[_climb(150.0)])        # no stitched_dists/elevs
    assert _rewrite_tss_block_from_stitched(STOCK_SUMMARY, rep) == STOCK_SUMMARY


# --- embed_in_prediction coverage-fallback warning --------------------------

def _write_pred(tmp_path):
    p = tmp_path / "route-prediction.md"
    p.write_text("# Route — Test\n\n## Summary\n- **Ascent**: 100 m\n")
    return p


def test_coverage_warning_inserted_below_threshold(tmp_path):
    p = _write_pred(tmp_path)
    rep = _report(climbs=[_climb()], coverage=50.0, elevs=[100.0] * 10,
                  dists=[float(i) for i in range(10)])
    embed_in_prediction(p, rep)
    txt = p.read_text()
    assert "BEGIN COVERAGE-WARN" in txt
    assert "50.0% below 80% threshold" in txt
    # idempotent: a second pass keeps exactly one warning block
    embed_in_prediction(p, rep)
    assert p.read_text().count("BEGIN COVERAGE-WARN") == 1


def test_no_coverage_warning_at_or_above_threshold(tmp_path):
    p = _write_pred(tmp_path)
    rep = _report(climbs=[_climb()], coverage=90.0, elevs=[100.0] * 10,
                  dists=[float(i) for i in range(10)])
    embed_in_prediction(p, rep)
    assert "BEGIN COVERAGE-WARN" not in p.read_text()
