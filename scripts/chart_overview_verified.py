"""Full-route overview chart with hi-fidelity-into-GPX stitched profile.

Top panel: GPX-only elevation (grey) vs Petrasova-blended stitched
profile (blue). The stitched profile equals GPX outside climbs, hi-fi
1m-lidar @ 5m samples inside climbs, with a 75m blend zone smoothing the
joins (Robinson DSF correction).

Bottom panel: smoothed gradient over the stitched profile. Red bars mark
sections steeper than 12%, orange bars 8-12%.

Usage:
    python scripts/chart_overview_verified.py routes/<route>.gpx
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_dem import LocalDEM
from elevation_fallback import GPXZClient
from verify_climbs import verify_route, smoothed_grades


def render(gpx_path: Path) -> Path:
    dem = LocalDEM(Path.home() / "cycling-coach-dem")
    fb = GPXZClient()

    report = verify_route(Path(gpx_path), dem, fallback=fb)
    if not report.stitched_dists:
        raise SystemExit("Verifier produced no stitched profile (no climbs verified).")

    # Re-parse for the GPX-only overlay (verify_route doesn't return raw GPX).
    from analyse_gpx import parse_gpx
    parsed = parse_gpx(str(gpx_path))
    gpx_d = parsed["dists"]
    gpx_e = parsed["eles"]
    name = parsed["name"]

    stitched_d = report.stitched_dists
    stitched_e = report.stitched_elevs

    # Convert metres → km for display.
    gpx_km = [d / 1000.0 for d in gpx_d]
    stitched_km = [d / 1000.0 for d in stitched_d]

    # Gradient on the stitched profile.
    grades = smoothed_grades(stitched_e, stitched_d, window_m=30.0)

    fig = plt.figure(figsize=(14, 7))
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1], hspace=0.08)
    ax_e = fig.add_subplot(gs[0])
    ax_g = fig.add_subplot(gs[1], sharex=ax_e)

    # Elevation panel
    ax_e.plot(gpx_km, gpx_e, "-", color="grey", lw=1.0, alpha=0.7, label="GPX waypoints (raw)")
    ax_e.plot(stitched_km, stitched_e, "-", color="tab:blue", lw=1.4, label="Stitched (hi-fi inside climbs, 75m Petrasova blend)")

    # Shade climb windows.
    for c in report.climbs:
        ax_e.axvspan(c.km_start, c.km_end, color="tab:red", alpha=0.08)
        ax_e.annotate(
            f"{c.verified_peak_pct:.1f}%  Δ{c.delta_pp:+.1f}",
            xy=((c.km_start + c.km_end) / 2,
                max(e for d, e in zip(stitched_d, stitched_e) if c.km_start * 1000 <= d <= c.km_end * 1000)),
            xytext=(0, 8), textcoords="offset points",
            ha="center", fontsize=8, color="tab:red",
        )

    ax_e.set_ylabel("elevation (m)")
    ax_e.set_title(
        f"Hi-fi overview — {name}\n"
        f"Verdict: {report.verdict.upper()} · "
        f"{sum(1 for v in report.climbs if v.delta_pp > 2.0)} climb(s) underestimated >2pp · "
        f"{len(report.missed_climbs)} missed climb(s)"
    )
    ax_e.legend(loc="upper left", fontsize=9)
    ax_e.grid(alpha=0.3)

    # Gradient strip
    for d_km, g in zip(stitched_km, grades):
        if g >= 12:
            ax_g.axvline(d_km, color="tab:red", alpha=0.25, lw=0.8)
        elif g >= 8:
            ax_g.axvline(d_km, color="orange", alpha=0.20, lw=0.8)
    ax_g.plot(stitched_km, grades, "-", color="black", lw=0.7)
    ax_g.axhline(0, color="grey", lw=0.5)
    ax_g.axhline(8, color="orange", lw=0.4, ls=":", alpha=0.5)
    ax_g.axhline(12, color="red", lw=0.4, ls=":", alpha=0.5)
    ax_g.set_xlabel("distance (km)")
    ax_g.set_ylabel("grade (%)")
    ax_g.grid(alpha=0.3)

    legend_handles = [
        mpatches.Patch(color="tab:red", alpha=0.25, label=">12%"),
        mpatches.Patch(color="orange", alpha=0.2, label="8–12%"),
    ]
    ax_g.legend(handles=legend_handles, loc="upper right", fontsize=8)

    out_dir = Path("rides/charts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(gpx_path).stem}-overview-verified.png"
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/chart_overview_verified.py <gpx>")
    p = render(Path(sys.argv[1]))
    print(f"Saved: {p}")
