"""Per-climb before/after chart: GPX waypoints vs hi-fi GPXZ-densified profile.

For each climb in a route, render a 2-row panel:
- Top: elevation profile (GPX raw waypoints in grey, verified 5m-densified in blue)
- Bottom: gradient profile (smoothed_grades on each)

Saves a single multi-panel PNG to rides/charts/<stem>-verify-compare.png.

Usage:
    python scripts/chart_verify_compare.py routes/<route>.gpx
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyse_gpx import parse_gpx, find_climbs
from elevation_fallback import GPXZClient
from local_dem import LocalDEM
from verify_climbs import (
    densify_polyline,
    haversine_m,
    smoothed_grades,
    detect_walls,
)


def _haversine_run(coords):
    out = [0.0]
    for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
        out.append(out[-1] + haversine_m(la1, lo1, la2, lo2))
    return out


def render(gpx_path: Path) -> Path:
    parsed = parse_gpx(str(gpx_path))
    lats, lons, eles, dists = (
        parsed["lats"], parsed["lons"], parsed["eles"], parsed["dists"]
    )
    climbs = find_climbs(dists, eles)
    if not climbs:
        raise SystemExit("No climbs found.")

    dem = LocalDEM(Path.home() / "cycling-coach-dem")
    fb = GPXZClient()
    if not fb.configured:
        raise SystemExit("GPXZ key not configured.")

    n = len(climbs)
    fig, axes = plt.subplots(n, 2, figsize=(14, 3.5 * n), squeeze=False)

    for row, c in enumerate(climbs):
        s_m, e_m = c["start_km"] * 1000, c["end_km"] * 1000

        # GPX raw points within climb window
        gpx_coords = [
            (la, lo, e, d) for la, lo, e, d in zip(lats, lons, eles, dists)
            if s_m <= d <= e_m
        ]
        gpx_d = [d - s_m for _, _, _, d in gpx_coords]
        gpx_e = [e for _, _, e, _ in gpx_coords]
        gpx_g = smoothed_grades(gpx_e, [d for _, _, _, d in gpx_coords], 30.0)

        # Verifier path: densify 5m, sample via GPXZ
        coords = [(la, lo) for la, lo, _, _ in gpx_coords]
        if len(coords) < 2:
            continue
        dense = densify_polyline(coords, stride_m=5.0)
        ver_e_raw = [dem.sample(la, lo) for la, lo in dense]
        miss = [i for i, e in enumerate(ver_e_raw) if e is None]
        if miss:
            filled = fb.sample_polyline([dense[i] for i in miss])
            for i, v in zip(miss, filled):
                ver_e_raw[i] = float(v)
        ver_e = [float(e) for e in ver_e_raw]
        ver_d_full = _haversine_run(dense)
        ver_g = smoothed_grades(ver_e, ver_d_full, 30.0)

        gpx_peak = max(gpx_g) if gpx_g else 0.0
        ver_peak = max(ver_g) if ver_g else 0.0

        # Walls — 15m smoothing for sharp wall boundaries, on the hi-fi series.
        ver_g_walls = smoothed_grades(ver_e, ver_d_full, 15.0)
        walls = detect_walls(ver_g_walls, ver_d_full)

        # Elevation panel
        ax_e = axes[row, 0]
        ax_e.plot(gpx_d, gpx_e, "o-", color="grey", alpha=0.7, label="GPX waypoints", markersize=4)
        ax_e.plot(ver_d_full, ver_e, "-", color="tab:blue", lw=1.5, label="Hi-fi (1m lidar @ 5m)")
        for w in walls:
            ax_e.axvspan(w['offset_m'], w['offset_m'] + w['length_m'],
                         color='#C8302C', alpha=0.18, zorder=1)
            ax_e.text(w['offset_m'] + w['length_m'] / 2,
                      max(ver_e),
                      f"⚠ {w['peak_pct']:.0f}%\n{w['length_m']:.0f}m",
                      ha='center', va='top', fontsize=7, fontweight='bold',
                      color='#7A1F1F', zorder=5)
        ax_e.set_xlabel("metres along climb")
        ax_e.set_ylabel("elevation (m)")
        ax_e.set_title(
            f"Climb {row+1}: km {c['start_km']:.2f}–{c['end_km']:.2f}  "
            f"({c['end_km']-c['start_km']:.2f} km)"
        )
        ax_e.legend(loc="lower right", fontsize=8)
        ax_e.grid(alpha=0.3)

        # Gradient panel
        ax_g = axes[row, 1]
        ax_g.plot(gpx_d, gpx_g, "o-", color="grey", alpha=0.7, label=f"GPX  peak {gpx_peak:.1f}%", markersize=4)
        ax_g.plot(ver_d_full, ver_g, "-", color="tab:red", lw=1.5, label=f"Hi-fi  peak {ver_peak:.1f}%")
        for w in walls:
            ax_g.axvspan(w['offset_m'], w['offset_m'] + w['length_m'],
                         color='#C8302C', alpha=0.18, zorder=1)
        ax_g.axhline(0, color="k", lw=0.5)
        ax_g.axhline(8, color="orange", lw=0.5, ls=":", alpha=0.5)
        ax_g.axhline(12, color="red", lw=0.5, ls=":", alpha=0.5)
        ax_g.set_xlabel("metres along climb")
        ax_g.set_ylabel("gradient (%)")
        ax_g.set_title(f"Δ peak: {ver_peak - gpx_peak:+.1f}pp")
        ax_g.legend(loc="upper right", fontsize=8)
        ax_g.grid(alpha=0.3)

    fig.suptitle(f"GPX vs hi-fi model — {parsed['name']}", fontsize=13, y=1.0)
    fig.tight_layout()

    out_dir = Path("rides/charts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(gpx_path).stem}-verify-compare.png"
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/chart_verify_compare.py <gpx>")
    p = render(Path(sys.argv[1]))
    print(f"Saved: {p}")
