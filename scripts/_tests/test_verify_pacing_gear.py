"""Unit tests for gear suggestions in the hi-fi pacing path (verify_climbs).

Tests:
  1. _compute_pacing returns gear_ftp/gear_map/gear_z3 as 3-tuples when
     called with the tripster bike (which has gearing).
  2. render_report includes the compact "Gear @60-75 rpm" line and RPM token
     when verified_pacing carries gear data.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest

from bike_config import load_bike
from verify_climbs import (
    _compute_pacing,
    _fmt_gear,
    render_report,
    FidelityReport,
    ClimbVerification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_climb(length_m=600, avg_pct=8.0):
    """Return (dists, elevs) for a constant-grade climb."""
    n = 61
    dists = np.linspace(0, length_m, n).tolist()
    elevs = [avg_pct / 100.0 * d for d in dists]
    return dists, elevs


# ---------------------------------------------------------------------------
# Part B.1 — _compute_pacing stores gear tuples
# ---------------------------------------------------------------------------

def test_compute_pacing_gear_tuples():
    bike = load_bike("tripster")
    dists, elevs = _synthetic_climb(600, 8.0)
    result = _compute_pacing(dists, elevs, peak_pct=10.0, bike=bike, surface="tarmac")

    assert result, "_compute_pacing returned empty dict"

    # Gear fields must be present
    assert "gear_ftp" in result, "gear_ftp missing from _compute_pacing result"
    assert "gear_map" in result, "gear_map missing from _compute_pacing result"
    assert "gear_z3"  in result, "gear_z3 missing from _compute_pacing result"

    # For tripster (has gearing) each should be a 3-tuple of (int, int, float)
    for key in ("gear_ftp", "gear_map", "gear_z3"):
        g = result[key]
        assert g is not None, f"{key} is None for a bike with gearing"
        assert len(g) == 3, f"{key} should be a 3-tuple, got {g!r}"
        cr, cog, rpm = g
        assert isinstance(cr, int), f"chainring should be int, got {cr!r}"
        assert isinstance(cog, int), f"cog should be int, got {cog!r}"
        assert isinstance(rpm, float), f"rpm should be float, got {rpm!r}"
        assert 20 < rpm < 200, f"rpm {rpm} looks implausible"


def test_compute_pacing_no_gearing_bike():
    """A bike without gearing should return None for all gear fields."""
    bike = load_bike("brompton_g")
    dists, elevs = _synthetic_climb(600, 5.0)
    result = _compute_pacing(dists, elevs, peak_pct=7.0, bike=bike, surface="tarmac")
    if not result:
        pytest.skip("brompton_g returned empty pacing (no power meter?)")
    for key in ("gear_ftp", "gear_map", "gear_z3"):
        assert key in result, f"{key} missing"
        assert result[key] is None, f"expected None for no-gearing bike, got {result[key]!r}"


# ---------------------------------------------------------------------------
# Part B.2 — render_report emits gear lines
# ---------------------------------------------------------------------------

def _make_report_with_gear():
    """Build a minimal FidelityReport with one climb that has gear data."""
    bike = load_bike("tripster")
    dists, elevs = _synthetic_climb(600, 8.0)
    pacing = _compute_pacing(dists, elevs, peak_pct=10.0, bike=bike, surface="tarmac")
    assert pacing, "pacing must be non-empty for this test"

    cv = ClimbVerification(
        name="Test climb",
        km_start=5.0,
        km_end=5.6,
        gpx_peak_pct=9.5,
        verified_peak_pct=10.0,
        delta_pp=0.5,
        length_above_8=200.0,
        length_above_10=100.0,
        length_above_12=0.0,
        length_above_14=0.0,
        fallback_used=False,
        verified_pacing=pacing,
    )

    return FidelityReport(
        route_name="test_route",
        backend="test",
        coverage_pct=100.0,
        climbs=[cv],
        verdict="safe",
    )


def test_render_report_contains_gear_line():
    report = _make_report_with_gear()
    output = render_report(report)

    assert "Gear @60" in output or "gear @60" in output, \
        f"Expected 'Gear @60' in render_report output:\n{output}"
    assert "rpm" in output.lower(), \
        f"Expected 'rpm' in render_report output:\n{output}"

    import re
    assert re.search(r'\d+x\d+', output), \
        f"Expected gear notation NxM in render_report output:\n{output}"
    assert re.search(r'\d+x\d+ \(\d+\)', output), \
        f"Expected compact gear format NxM (RPM) in render_report output:\n{output}"


def test_fmt_gear_none():
    assert _fmt_gear(None) is None


def test_fmt_gear_formats_correctly():
    result = _fmt_gear((34, 28, 69.7))
    assert result == "34x28 (70)", f"Unexpected _fmt_gear output: {result!r}"
