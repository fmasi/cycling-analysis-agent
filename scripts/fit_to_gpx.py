"""Export a FIT file's 1 Hz GPS+altitude trace as a GPX track.

Use when you want to feed an actually-ridden FIT into a GPX-only pipeline
(analyse_gpx, verify_climbs, chart_overview). The output is a single-segment
GPX with one trkpt per FIT record that has both lat/lon and altitude.

Usage:
    python scripts/fit_to_gpx.py <path/to/ride.fit>
        -> writes routes/<stem>-trace.gpx
    python scripts/fit_to_gpx.py <path/to/ride.fit> --out my-trace.gpx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyse_fit import parse_fit

SEMI_TO_DEG = 180.0 / (1 << 31)


def fit_to_gpx(fit_path: Path, out_path: Path, name: str | None = None) -> Path:
    """Convert FIT to single-segment GPX. Returns out_path."""
    sess, recs, _ = parse_fit(str(fit_path))
    pts: list[tuple[float, float, float]] = []  # (lat, lon, ele)
    for r in recs:
        la = r.get("position_lat")
        lo = r.get("position_long")
        alt = r.get("enhanced_altitude", r.get("altitude"))
        if la is None or lo is None or alt is None:
            continue
        pts.append((la * SEMI_TO_DEG, lo * SEMI_TO_DEG, float(alt)))
    if not pts:
        raise SystemExit(f"No GPS+altitude records in {fit_path}")

    track_name = name or fit_path.stem
    body = "\n".join(
        f'      <trkpt lat="{la:.7f}" lon="{lo:.7f}"><ele>{e:.2f}</ele></trkpt>'
        for la, lo, e in pts
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="fit_to_gpx" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f'  <trk><name>{track_name}</name><trkseg>\n'
        f'{body}\n'
        '  </trkseg></trk>\n'
        '</gpx>\n'
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(xml)
    return out_path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fit", type=Path, help="Path to .fit file")
    p.add_argument("--out", type=Path, default=None,
                   help="Output .gpx path (default: routes/<stem>-trace.gpx)")
    p.add_argument("--name", default=None,
                   help="Track name attribute (default: file stem)")
    args = p.parse_args(argv)

    out = args.out or (Path("routes") / f"{args.fit.stem}-trace.gpx")
    written = fit_to_gpx(args.fit, out, name=args.name)
    sess, recs, _ = parse_fit(str(args.fit))
    n_pts = sum(1 for r in recs if r.get("position_lat") is not None)
    print(f"Wrote {written}  ({n_pts} points)")


if __name__ == "__main__":
    main()
