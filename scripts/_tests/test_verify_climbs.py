import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_climbs import (
    densify_polyline,
    longest_run_above,
    classify_verdict,
    haversine_m,
    verify_route,
    render_report,
    embed_in_prediction,
    mean_max_grade,
    detect_walls,
    FidelityReport,
    ClimbVerification,
)


def test_densify_polyline_5m_stride():
    # ~111m segment at the equator
    coords = [(0.0, 0.0), (0.0, 0.001)]
    out = densify_polyline(coords, stride_m=5.0)
    # ~111m / 5m + 1 endpoint
    assert 21 <= len(out) <= 24
    assert out[0] == coords[0]
    assert out[-1] == coords[-1]


def test_longest_run_above_simple():
    # distances every 10m, gradients pattern: [5,9,11,13,12,8,7]
    grades = [5, 9, 11, 13, 12, 8, 7]
    dists = [i * 10.0 for i in range(len(grades))]
    assert longest_run_above(grades, dists, threshold=10) == 30.0  # idx 2..4
    assert longest_run_above(grades, dists, threshold=12) == 20.0  # idx 3..4
    assert longest_run_above(grades, dists, threshold=20) == 0.0


def test_mean_max_grade_constant_slope():
    # 1km at 8% (1m stride, 80m gain)
    dists = [float(i) for i in range(1001)]
    elevs = [0.08 * d for d in dists]
    # Any window length up to 1km should give ~8%
    assert abs(mean_max_grade(elevs, dists, 25.0) - 8.0) < 0.01
    assert abs(mean_max_grade(elevs, dists, 500.0) - 8.0) < 0.01
    # Longer than profile -> None
    assert mean_max_grade(elevs, dists, 2000.0) is None


def test_mean_max_grade_picks_steepest_window():
    # 500m flat, then 100m wall at 15%, then 500m flat
    dists = [float(i) for i in range(1101)]
    elevs = []
    for d in dists:
        if d < 500: elevs.append(0.0)
        elif d < 600: elevs.append(0.15 * (d - 500))
        else: elevs.append(15.0)
    # peak-25m should hit ~15% (window fits in the wall)
    assert mean_max_grade(elevs, dists, 25.0) > 14.5
    # peak-100m should hit ~15% (window equals wall length)
    assert mean_max_grade(elevs, dists, 100.0) > 14.5
    # peak-500m can't avoid the flats -> averages down
    assert mean_max_grade(elevs, dists, 500.0) < 5.0


def test_detect_walls_finds_segment_above_threshold():
    # 200m at 5%, then 50m wall at 12%, then 200m at 5%
    dists = [float(i) for i in range(451)]
    grades = []
    for d in dists:
        if d < 200: grades.append(5.0)
        elif d < 250: grades.append(12.0)
        else: grades.append(5.0)
    walls = detect_walls(grades, dists, threshold_pct=10.0, min_length_m=30.0,
                         total_length_m=450.0)
    assert len(walls) == 1
    w = walls[0]
    assert abs(w["offset_m"] - 200) < 1
    assert abs(w["length_m"] - 49) <= 1
    assert abs(w["peak_pct"] - 12.0) < 0.01
    assert 40 < w["pct_in"] < 50  # 200/450 ≈ 44%


def test_detect_walls_ignores_short_spikes():
    # 10m spike at 15% — below min_length_m
    dists = [float(i) for i in range(101)]
    grades = [15.0 if 40 <= d <= 50 else 5.0 for d in dists]
    walls = detect_walls(grades, dists, threshold_pct=10.0, min_length_m=30.0)
    assert walls == []


def test_classify_verdict():
    assert classify_verdict(deltas=[0.5, -0.5], missed=0) == "safe"
    assert classify_verdict(deltas=[1.5, 0.0], missed=0) == "minor"
    assert classify_verdict(deltas=[3.0, 0.0], missed=0) == "high"
    assert classify_verdict(deltas=[0.0], missed=1) == "high"


class FakeDEM:
    """Synthetic DEM along a north-going meridian from (lat0, lon0).

    Baseline elevation: 5% slope (50 m per km of route distance).
    Spike: extra +10 m centred at offset 7000 m, applied linearly between
    6900 m and 7000 m (rising) then 7000 m and 7100 m (falling). The 100 m
    rise of 10 m yields a local 10% spike on top of the 5% baseline -> ~15%
    peak, well above the 12% test threshold.
    """

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0

    def _offset_m(self, lat: float, lon: float) -> float:
        return haversine_m(self.lat0, self.lon0, lat, lon)

    def sample(self, lat: float, lon: float):
        d = self._offset_m(lat, lon)
        base = 0.05 * d  # 5% baseline grade
        if 6900.0 <= d <= 7000.0:
            spike = 10.0 * (d - 6900.0) / 100.0
        elif 7000.0 < d <= 7100.0:
            spike = 10.0 * (7100.0 - d) / 100.0
        else:
            spike = 0.0
        return base + spike

    def covers(self, lat, lon):
        return True

    def sample_polyline(self, coords, stride_m=5.0):
        return [self.sample(la, lo) for la, lo in coords]


def _write_synthetic_gpx(path, lat0=51.0, lon0=0.0, length_m=8000.0, n=200):
    """Write a north-going GPX with a 5% slope (gain 400m over 8km)."""
    # 1 deg lat ~= 111320 m
    dlat_total = length_m / 111320.0
    pts = []
    for i in range(n):
        t = i / (n - 1)
        lat = lat0 + dlat_total * t
        lon = lon0
        ele = 0.05 * length_m * t  # 5% baseline
        pts.append((lat, lon, ele))
    body = "\n".join(
        f'      <trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele:.2f}</ele></trkpt>'
        for lat, lon, ele in pts
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">\n'
        '  <trk><name>spike-test</name><trkseg>\n'
        f'{body}\n'
        '  </trkseg></trk>\n'
        '</gpx>\n'
    )
    path.write_text(xml)


def test_verify_route_flags_spike(tmp_path):
    gpx = tmp_path / "spike.gpx"
    _write_synthetic_gpx(gpx, lat0=51.0, lon0=0.0)
    dem = FakeDEM(lat0=51.0, lon0=0.0)
    report = verify_route(gpx, dem, fallback=None)

    assert len(report.climbs) >= 1
    # The climb that contains the spike at offset ~7 km
    spike_climb = None
    for c in report.climbs:
        if c.km_start * 1000 <= 7000 <= c.km_end * 1000:
            spike_climb = c
            break
    assert spike_climb is not None, "no climb covering the 7 km spike"
    assert spike_climb.verified_peak_pct > 12.0
    assert spike_climb.delta_pp > 2.0
    assert report.verdict == "high"


class FlatGpxClimbingDEM:
    """GPX altitudes are flat (no climb visible to find_climbs) but the DEM
    has a 400m-long 8% hidden hill at offset 2000-2400m. Tests the two-pass
    missed-climb pipeline: Pass 1 finds it, Pass 2 re-samples accurately.
    """
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0

    def sample(self, lat, lon):
        d = haversine_m(self.lat0, self.lon0, lat, lon)
        if 2000.0 <= d <= 2400.0:
            return 100.0 + 0.08 * (d - 2000.0)  # 8% over 400m, +32m gain
        if d > 2400.0:
            return 132.0  # plateau after the hidden climb
        return 100.0

    def covers(self, lat, lon):
        return True

    def sample_polyline(self, coords, stride_m=5.0):
        return [self.sample(la, lo) for la, lo in coords]


def _write_flat_gpx(path, lat0=51.0, lon0=0.0, length_m=4000.0, n=80):
    """GPX with constant altitude 100m — find_climbs cannot detect any climb."""
    dlat_total = length_m / 111320.0
    pts = [(lat0 + dlat_total * i / (n - 1), lon0, 100.0) for i in range(n)]
    body = "\n".join(
        f'      <trkpt lat="{la:.7f}" lon="{lo:.7f}"><ele>{e:.2f}</ele></trkpt>'
        for la, lo, e in pts
    )
    path.write_text(
        '<?xml version="1.0"?><gpx version="1.1" '
        'xmlns="http://www.topografix.com/GPX/1/1">'
        f'<trk><name>flat</name><trkseg>{body}</trkseg></trk></gpx>'
    )


def test_detect_missed_climbs_returns_ClimbVerification_with_fine_peak(tmp_path):
    """Pass 1 finds the hidden 8% hill; Pass 2 should re-sample it and
    populate verified_peak_pct, mean_max, etc. — same shape as declared climbs."""
    gpx = tmp_path / "flat.gpx"
    _write_flat_gpx(gpx)
    dem = FlatGpxClimbingDEM(lat0=51.0, lon0=0.0)
    report = verify_route(gpx, dem, fallback=None)

    # find_climbs on a flat GPX may produce noisy pseudo-climbs near the edges
    # (km 0.x with 0% peak) — we don't assert about declared climbs.
    # The real test: detect_missed_climbs must catch the hidden 8% hill and
    # return it as a full ClimbVerification (Phase A two-pass result).
    assert len(report.missed_climbs) >= 1
    hidden = next(
        (cv for cv in report.missed_climbs if 1.8 <= cv.km_start <= 2.3),
        None,
    )
    assert hidden is not None, (
        f"No missed climb near km 2.0: got "
        f"{[(cv.km_start, cv.verified_peak_pct) for cv in report.missed_climbs]}"
    )
    assert isinstance(hidden, ClimbVerification)
    # The fine-pass peak should be ≥ 6% (true grade is 8% sustained over 400m).
    assert hidden.verified_peak_pct >= 6.0, (
        f"Fine pass should see ~8%, got {hidden.verified_peak_pct:.1f}%"
    )
    # Mean-max curve populated like a declared climb (Phase A integration).
    assert hidden.mean_max
    assert hidden.mean_max.get("peak_100m") is not None
    assert hidden.mean_max["peak_100m"] >= 6.0


def test_classify_verdict_flags_missed_as_high():
    from verify_climbs import classify_verdict
    assert classify_verdict([0.2, 0.5], missed=0) == "safe"
    assert classify_verdict([1.5], missed=0) == "minor"
    assert classify_verdict([3.0], missed=0) == "high"
    # An unverified (coverage-gap) climb is passed as a "missed" count → high.
    assert classify_verdict([], missed=1) == "high"


def test_render_report_marks_unverified_climb():
    # A total DEM-miss climb carries NaN peak/delta; it must render
    # "(unverified)", never a benign 0.0%/large-negative-delta row.
    cv = ClimbVerification(
        name="C1", km_start=12.0, km_end=13.0,
        gpx_peak_pct=9.0, verified_peak_pct=float("nan"), delta_pp=float("nan"),
        length_above_8=0.0, length_above_10=0.0, length_above_12=0.0,
        length_above_14=0.0, fallback_used=False,
    )
    report = FidelityReport(
        route_name="t", backend="local-dem", coverage_pct=10.0,
        climbs=[cv], missed_climbs=[], verdict="high",
    )
    txt = render_report(report)
    assert "(unverified)" in txt
    assert "0.0%" not in txt.split("Per-climb")[1].split("###")[0]  # no fake 0% row


def test_render_report_includes_verified_pacing_when_present():
    cv = ClimbVerification(
        name="C1", km_start=7.0, km_end=8.5,
        gpx_peak_pct=9.3, verified_peak_pct=14.0, delta_pp=4.7,
        length_above_8=200, length_above_10=120, length_above_12=80,
        length_above_14=0, fallback_used=True,
        verified_pacing={
            "length_m": 1500, "gain_m": 105, "avg_pct": 7.0, "peak_pct": 14.0,
            "speed_ftp_kmh": 10.5, "speed_map_kmh": 12.8, "speed_z3_kmh": 8.1,
            "duration_ftp_min": 8.6, "duration_map_min": 7.0,
            "vam_ftp": 730, "survival_w": 240,
        },
    )
    report = FidelityReport(
        route_name="t", backend="local-dem", coverage_pct=100.0,
        climbs=[cv], missed_climbs=[], verdict="high",
    )
    txt = render_report(report)
    assert "Hi-fi pacing" in txt
    assert "10.5" in txt  # speed @ FTP
    assert "Survive (W)" in txt
    assert "240" in txt   # survival watt value


def test_render_report_includes_mean_max_and_walls():
    cv = ClimbVerification(
        name="C1", km_start=12.5, km_end=15.8,
        gpx_peak_pct=7.0, verified_peak_pct=12.4, delta_pp=5.4,
        length_above_8=420, length_above_10=90, length_above_12=12,
        length_above_14=0, fallback_used=False,
        mean_max={
            "peak_25m": 12.4, "peak_100m": 10.2,
            "peak_500m": 8.1, "peak_1km": 7.5,
        },
        walls=[
            {"offset_m": 800, "length_m": 60, "peak_pct": 12.4, "pct_in": 24.0},
            {"offset_m": 2100, "length_m": 30, "peak_pct": 10.8, "pct_in": 64.0},
        ],
    )
    report = FidelityReport(
        route_name="t", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    txt = render_report(report)
    assert "Gradient profile" in txt
    assert "peak-25m" in txt and "peak-1km" in txt
    assert "12.4%" in txt and "7.5%" in txt
    assert "Walls" in txt
    assert "+800" in txt and "60 m" in txt and "24% in" in txt
    assert "+2100" in txt


def test_render_report_skips_mean_max_section_when_absent():
    cv = ClimbVerification(
        name="C1", km_start=0, km_end=1, gpx_peak_pct=5, verified_peak_pct=5,
        delta_pp=0, length_above_8=0, length_above_10=0, length_above_12=0,
        length_above_14=0, fallback_used=False,
    )
    report = FidelityReport(
        route_name="t", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    txt = render_report(report)
    assert "Gradient profile" not in txt
    assert "Walls" not in txt


def test_render_report_includes_verdict_and_table():
    cv = ClimbVerification(
        name="C1", km_start=6.75, km_end=8.70,
        gpx_peak_pct=9.3, verified_peak_pct=14.3, delta_pp=5.0,
        length_above_8=248, length_above_10=192, length_above_12=116,
        length_above_14=4, fallback_used=False,
    )
    report = FidelityReport(
        route_name="test", backend="local-1m", coverage_pct=100.0,
        climbs=[cv], missed_climbs=[], verdict="high",
    )
    text = render_report(report)
    assert "HIGH RISK" in text or "high" in text.lower()
    assert "14.3" in text
    assert "+5.0" in text or "5.0pp" in text
    assert "<!-- BEGIN FIDELITY -->" in text
    assert "<!-- END FIDELITY -->" in text


def test_embed_in_prediction_strips_gpx_pacing_when_hifi_present(tmp_path):
    md = tmp_path / "x-prediction.md"
    md.write_text(
        "# Route\n\n## Climbs (1)\n\n### Climb 1: km 0 – 1\n\n"
        "- **Length**: 1000 m | **Gain**: 50 m\n"
        "<!-- BEGIN GPX-PACING -->\n"
        "- **Speed @ FTP (171W)**: 12.0 km/h\n"
        "- **Pacing**: MAP zone\n"
        "<!-- END GPX-PACING -->\n\n"
    )
    cv = ClimbVerification(
        name="C1", km_start=0, km_end=1, gpx_peak_pct=5, verified_peak_pct=8,
        delta_pp=3, length_above_8=0, length_above_10=0, length_above_12=0,
        length_above_14=0, fallback_used=False,
        verified_pacing={
            "length_m": 1000, "gain_m": 50, "avg_pct": 5.0, "peak_pct": 8.0,
            "speed_ftp_kmh": 11.0, "speed_map_kmh": 13.0, "speed_z3_kmh": 9.0,
            "duration_ftp_min": 5.5, "duration_map_min": 4.6,
            "vam_ftp": 600, "survival_w": 180,
        },
    )
    report = FidelityReport(
        route_name="x", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    embed_in_prediction(md, report)
    text = md.read_text()
    assert "GPX-PACING" not in text
    assert "Speed @ FTP (171W)" not in text
    assert "Hi-fi pacing" in text


def test_embed_in_prediction_keeps_gpx_pacing_when_no_hifi(tmp_path):
    md = tmp_path / "x-prediction.md"
    md.write_text(
        "# Route\n\n### Climb 1: km 0 – 1\n\n"
        "<!-- BEGIN GPX-PACING -->\n"
        "- **Pacing**: MAP zone\n"
        "<!-- END GPX-PACING -->\n"
    )
    cv = ClimbVerification(
        name="C1", km_start=0, km_end=1, gpx_peak_pct=5, verified_peak_pct=5,
        delta_pp=0, length_above_8=0, length_above_10=0, length_above_12=0,
        length_above_14=0, fallback_used=False,
    )
    report = FidelityReport(
        route_name="x", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    embed_in_prediction(md, report)
    text = md.read_text()
    assert "GPX-PACING" in text
    assert "MAP zone" in text


def test_embed_in_prediction_idempotent(tmp_path):
    md = tmp_path / "x-prediction.md"
    md.write_text("# Route\n\n## TSS estimate\nfoo\n")
    cv = ClimbVerification(
        name="C1", km_start=0, km_end=1, gpx_peak_pct=5, verified_peak_pct=5,
        delta_pp=0, length_above_8=0, length_above_10=0, length_above_12=0,
        length_above_14=0, fallback_used=False,
    )
    report = FidelityReport(
        route_name="x", backend="local-1m", coverage_pct=100,
        climbs=[cv], missed_climbs=[], verdict="safe",
    )
    embed_in_prediction(md, report)
    embed_in_prediction(md, report)  # second call should not duplicate
    assert md.read_text().count("<!-- BEGIN FIDELITY -->") == 1


from verify_climbs import resolve_coverage_policy


def test_resolve_policy_explicit_flag():
    assert resolve_coverage_policy(flag="api", interactive=True, has_key=True) == "api"


def test_resolve_policy_default_interactive():
    assert resolve_coverage_policy(flag=None, interactive=True, has_key=False) == "prompt"


def test_resolve_policy_default_non_interactive_with_key():
    assert resolve_coverage_policy(flag=None, interactive=False, has_key=True) == "api"


def test_resolve_policy_default_non_interactive_no_key():
    assert resolve_coverage_policy(flag=None, interactive=False, has_key=False) == "skip"


# -----------------------------------------------------------------
# stitch_profile: Petrasova-style distance-weighted blend zones
# -----------------------------------------------------------------

from verify_climbs import stitch_profile


def test_stitch_profile_no_climbs_returns_gpx_unchanged():
    gpx_d = [0.0, 100.0, 200.0, 300.0]
    gpx_e = [10.0, 15.0, 20.0, 30.0]
    out_d, out_e = stitch_profile(gpx_d, gpx_e, climb_segments=[])
    assert out_d == gpx_d
    assert out_e == gpx_e


def test_stitch_profile_replaces_climb_interior_with_verified():
    """Climb interior must be exactly the verified samples (no GPX leakage)."""
    gpx_d = [i * 100.0 for i in range(10)]  # 0..900m
    gpx_e = [10.0] * 10  # flat GPX, no peak
    # Verified samples inside km 0.3-0.5 reveal a sharp wall to 30m
    ver_d = [300.0, 350.0, 400.0, 450.0, 500.0]
    ver_e = [10.0, 20.0, 30.0, 20.0, 10.0]
    out_d, out_e = stitch_profile(
        gpx_d, gpx_e,
        climb_segments=[(300.0, 500.0, ver_d, ver_e)],
        blend_m=0.0,  # no blend zone for this test
    )
    # The peak (30m at mid-climb) must be present in output
    assert max(out_e) == 30.0
    # And the verified samples must appear exactly inside [300, 500]
    interior = [(d, e) for d, e in zip(out_d, out_e) if 300.0 <= d <= 500.0]
    assert (400.0, 30.0) in interior


def test_stitch_profile_blend_zone_is_continuous():
    """No discontinuity at the join when GPX disagrees with verified by a few m."""
    # GPX says flat 10m everywhere.
    gpx_d = [i * 10.0 for i in range(101)]  # 0..1000m at 10m
    gpx_e = [10.0] * 101
    # Verified inside [400, 600] says it's actually 15m baseline with a 30m wall.
    ver_d = [float(d) for d in range(400, 601, 5)]
    ver_e = [15.0 if d != 500 else 30.0 for d in range(400, 601, 5)]
    out_d, out_e = stitch_profile(
        gpx_d, gpx_e,
        climb_segments=[(400.0, 600.0, ver_d, ver_e)],
        blend_m=50.0,
    )
    # Find values around boundary at 400m
    boundary = [(d, e) for d, e in zip(out_d, out_e) if 340.0 <= d <= 460.0]
    # Step-to-step diff inside the blend zone should be small (no >5m jumps)
    diffs = [abs(boundary[i+1][1] - boundary[i][1]) for i in range(len(boundary)-1)]
    assert max(diffs) < 5.0, f"jumpy boundary: max diff {max(diffs):.2f}m"


def test_stitch_profile_preserves_far_field_gpx():
    """Points far from any climb must equal the original GPX elevation."""
    gpx_d = [i * 100.0 for i in range(20)]  # 0..1900m
    gpx_e = [float(i) for i in range(20)]   # rising 0..19m
    ver_d = [800.0, 850.0, 900.0, 950.0, 1000.0]
    ver_e = [15.0, 25.0, 35.0, 25.0, 15.0]
    out_d, out_e = stitch_profile(
        gpx_d, gpx_e,
        climb_segments=[(800.0, 1000.0, ver_d, ver_e)],
        blend_m=50.0,
    )
    # Far point at d=100 should be untouched
    idx = out_d.index(100.0)
    assert out_e[idx] == 1.0
    # Far point at d=1800 should be untouched
    idx = out_d.index(1800.0)
    assert out_e[idx] == 18.0
