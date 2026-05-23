import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subprocess

ROOT = Path(__file__).resolve().parents[2]
PY = "/opt/miniconda3/envs/cycling/bin/python"


def _write_gpx(path):
    # ~1.2km: 600m flat then 600m climbing ~10% (steep -> qualifies lo-fi gate).
    pts = []
    lat = 51.0
    ele = 20.0
    for i in range(120):
        lat += 0.00009  # ~10m per step
        if i >= 60:
            ele += 1.0   # +1m per 10m = 10%
        pts.append(f'<trkpt lat="{lat:.6f}" lon="-0.1"><ele>{ele:.1f}</ele></trkpt>')
    path.write_text(
        '<?xml version="1.0"?>'
        '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg>'
        + "".join(pts) + "</trkseg></trk></gpx>")


def test_climb_detail_chart_generated_lofi(tmp_path):
    gpx = tmp_path / "steeptest.gpx"
    _write_gpx(gpx)
    charts = tmp_path / "charts"
    charts.mkdir()
    cmd = [PY, "scripts/analyse_gpx.py", "--bike", "tripster", "--surface",
           "tarmac", "--save", "--no-verify", "--climb-detail", "all",
           "--chart-dir", str(charts), str(gpx)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 0, res.stderr
    pngs = list(charts.glob("steeptest-climb*.png"))
    assert pngs, f"no per-climb png; stdout={res.stdout}\nstderr={res.stderr}"


def test_pacing_has_gear_and_rpm(tmp_path):
    gpx = tmp_path / "steeptest2.gpx"
    _write_gpx(gpx)
    charts = tmp_path / "charts2"
    charts.mkdir()
    cmd = [PY, "scripts/analyse_gpx.py", "--bike", "tripster", "--surface",
           "tarmac", "--save", "--no-verify", "--climb-detail", "all",
           "--chart-dir", str(charts), str(gpx)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 0, res.stderr
    md_path = ROOT / "routes" / "steeptest2-prediction.md"
    md = md_path.read_text()
    # Gear notation like "30x28" or "34x28", and "rpm" must appear
    assert "rpm" in md.lower(), f"'rpm' not found in pacing output:\n{md}"
    import re
    assert re.search(r'\d+x\d+', md), f"gear notation (NxM) not found:\n{md}"
    md_path.unlink()
