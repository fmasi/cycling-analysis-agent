"""Generate a zipped OSGB shapefile of a GPX route's bounding box.

The DEFRA Survey Data Download portal at https://environment.data.gov.uk/survey
lets you fetch UK LIDAR tiles by uploading a polygon shapefile (.shp/.shx/
.dbf/.prj). This script produces exactly that — the four files inside one
zip — for any GPX or explicit bbox, in OSGB36 / British National Grid
projection (EPSG:27700) as the portal requires.

Useful as a workaround when the geostore.com REST API is unreachable (e.g.
because the user's IP is on a VPN ASN denylist).

Usage:
    python scripts/make_dem_shapefile.py --gpx routes/my-route.gpx
    python scripts/make_dem_shapefile.py --bbox -0.07,51.14,0.11,51.28
    python scripts/make_dem_shapefile.py --gpx ... --buffer-m 500 --out /tmp/area.zip
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import shapefile  # pyshp
from pyproj import CRS, Transformer


sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_util import bbox_from_gpx  # noqa: E402,F401  (shared; re-exported)


def wgs84_bbox_to_osgb_polygon(
    bbox: tuple[float, float, float, float], buffer_m: float = 0.0,
) -> list[tuple[float, float]]:
    """Project the WGS84 bbox corners to OSGB36, expand by buffer_m, and
    return a closed CCW polygon ring as (easting, northing) pairs.

    Projecting only the 4 corners is fine here — the bbox is tiny relative
    to the curvature of the British National Grid, so the projected polygon
    is rectangular enough for the portal's intersection check.
    """
    tr = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    minlon, minlat, maxlon, maxlat = bbox
    corners_lonlat = [
        (minlon, minlat),
        (maxlon, minlat),
        (maxlon, maxlat),
        (minlon, maxlat),
    ]
    proj = [tr.transform(lon, lat) for lon, lat in corners_lonlat]
    es = [p[0] for p in proj]
    ns = [p[1] for p in proj]
    e0, e1 = min(es) - buffer_m, max(es) + buffer_m
    n0, n1 = min(ns) - buffer_m, max(ns) + buffer_m
    # Closed CLOCKWISE outer ring. The shapefile spec (ESRI Whitepaper) says
    # outer rings MUST be clockwise — strict readers (DEFRA Survey portal)
    # treat a CCW ring as an inner hole, then reject the file as having no
    # outer polygon. Order: SW -> NW -> NE -> SE -> SW.
    return [(e0, n0), (e0, n1), (e1, n1), (e1, n0), (e0, n0)]


def write_shapefile_zip(
    polygon_osgb: list[tuple[float, float]], out_zip: Path, name: str,
) -> Path:
    """Write a single-polygon shapefile in OSGB36 and zip the four files."""
    tmp_dir = out_zip.parent / (out_zip.stem + "_shp_tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stem = tmp_dir / "area"

    w = shapefile.Writer(str(stem), shapeType=shapefile.POLYGON)
    # Minimal attribute schema — some OGR/Java parsers stumble on unexpected
    # fields. A single integer id is the safest "is there an attribute table"
    # signal.
    w.field("id", "N", size=4)
    w.poly([polygon_osgb])
    w.record(1)
    w.close()

    # Write the .prj using WKT1_ESRI ("British_National_Grid"). PROJCRS / WKT2
    # is the modern default but DEFRA's server-side parser (and many older
    # OGR builds) expect WKT1 — using WKT2 makes them treat the file as
    # missing a valid CRS, which then surfaces as a generic "single polygon"
    # error in the portal.
    prj_wkt = CRS.from_epsg(27700).to_wkt("WKT1_ESRI")
    (stem.with_suffix(".prj")).write_text(prj_wkt)

    # Zip the four members at the archive root (portal expects them there).
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            zf.write(stem.with_suffix(ext), arcname=f"area{ext}")

    # Tidy the temp dir.
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        p = stem.with_suffix(ext)
        if p.exists():
            p.unlink()
    tmp_dir.rmdir()
    return out_zip


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--gpx", type=Path)
    src.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat (WGS84)")
    p.add_argument(
        "--buffer-m", type=float, default=200.0,
        help="Expand bbox by N metres in OSGB so edge sampling is covered "
             "(default: 200m)",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Output .zip path (default: rides/charts/<name>-area.zip)",
    )
    p.add_argument("--name", default=None, help="Polygon attribute name")
    args = p.parse_args(argv)

    if args.gpx:
        bbox = bbox_from_gpx(args.gpx)
        stem = args.gpx.stem
    else:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        stem = "bbox"

    poly = wgs84_bbox_to_osgb_polygon(bbox, buffer_m=args.buffer_m)
    name = args.name or stem
    out_zip = args.out or (Path("rides/charts") / f"{stem}-area.zip")
    write_shapefile_zip(poly, out_zip, name=name)

    print(f"WGS84 bbox: {bbox}", file=sys.stderr)
    print(
        f"OSGB36 polygon (E,N): "
        f"min=({poly[0][0]:.0f},{poly[0][1]:.0f}) "
        f"max=({poly[2][0]:.0f},{poly[2][1]:.0f}) "
        f"buffer={args.buffer_m:.0f}m",
        file=sys.stderr,
    )
    print(f"Wrote: {out_zip}")


if __name__ == "__main__":
    main()
