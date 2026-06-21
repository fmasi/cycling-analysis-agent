"""Bulk DEM tile downloader for UK (DEFRA OGL v3) and France (IGN Etalab 2.0).

UK path: per OS-10km tile via the legacy Environment Agency geostore.com REST
API (anonymous). Two-step: catalog JSON → product GUID → zip of 5km GeoTIFFs.
Status: URL needs validation on a UK-routed network — geostore.com is firewalled
or simply unreachable from many non-UK IPs.

FR path: per-department .7z archive via the IGN Géoplateforme (data.geopf.fr).
Date suffix per dept resolved via the Atom feed. Extracts only the .asc tiles
intersecting the requested bbox and converts them to LZW-compressed GeoTIFF
so local_dem.py reads them with the existing UK code path.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional

import requests
from pyproj import Transformer

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# UK — legacy Environment Agency geostore REST API. Anonymous.
GEOSTORE_CATALOG = (
    "https://www.geostore.com/environment-agency/rest/product/"
    "OS_GB_10KM/{tile}?catalogName=Survey"
)
GEOSTORE_DOWNLOAD = (
    "https://www.geostore.com/environment-agency/rest/product/download/{guid}"
)

# FR — IGN Géoplateforme. Atom feed lists dated archives per department.
IGN_ATOM = "https://data.geopf.fr/telechargement/resource/RGEALTI"
IGN_DOWNLOAD = "https://data.geopf.fr/telechargement/download/RGEALTI"

# ---------------------------------------------------------------------------
# Presets: name -> (country, bbox, dept_code_or_None)
# ---------------------------------------------------------------------------
PRESETS: dict[str, tuple[str, tuple[float, float, float, float], Optional[str]]] = {
    "surrey-kent":     ("uk", (-0.6, 51.05, 0.6, 51.5), None),
    "greater-london":  ("uk", (-0.55, 51.30, 0.30, 51.70), None),
    "fontainebleau":   ("fr", (2.40, 48.30, 2.85, 48.55), "D077"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_util import bbox_from_gpx  # noqa: E402,F401  (shared; re-exported)


def _stream_download(
    url: str, dest: Path, chunk_mb: int = 4, max_retries: int = 5,
) -> Path:
    """Stream a (possibly large) file to dest atomically with resume support.

    If `dest.part` already exists, resumes via HTTP Range. On a dropped
    connection (ChunkedEncodingError / ConnectionError) retries up to
    `max_retries` times with the same resume mechanism.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    chunk = chunk_mb * 1024 * 1024
    attempt = 0
    while True:
        attempt += 1
        already = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Range": f"bytes={already}-"} if already else {}
        try:
            with requests.get(url, stream=True, timeout=120, headers=headers) as r:
                r.raise_for_status()
                # Compute total size: from Content-Range when resuming, else CL.
                size = 0
                cr = r.headers.get("content-range") or ""
                if "/" in cr:
                    size = int(cr.rsplit("/", 1)[-1])
                else:
                    size = already + int(r.headers.get("content-length") or 0)
                mode = "ab" if already else "wb"
                t0 = time.time()
                bytes_this_run = 0
                with tmp.open(mode) as f:
                    for buf in r.iter_content(chunk_size=chunk):
                        f.write(buf)
                        bytes_this_run += len(buf)
                        total = already + bytes_this_run
                        if size and bytes_this_run % (256 * 1024 * 1024) < chunk:
                            el = time.time() - t0
                            pct = 100.0 * total / size
                            print(
                                f"  {total/1e9:.2f}/{size/1e9:.2f} GB "
                                f"({pct:.0f}%) {bytes_this_run/1e6/max(el,0.1):.1f} MB/s",
                                file=sys.stderr,
                            )
            tmp.rename(dest)
            return dest
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError) as e:
            if attempt >= max_retries:
                raise
            print(
                f"  ⚠ connection dropped after "
                f"{tmp.stat().st_size/1e9:.2f} GB (attempt {attempt}/{max_retries}): "
                f"{type(e).__name__}. Retrying in 5s...",
                file=sys.stderr,
            )
            time.sleep(5)


# ===========================================================================
# UK — OS grid + Environment Agency geostore
# ===========================================================================

def os_grid_tiles_for_bbox(bbox) -> list[str]:
    """Return the 10km OS grid squares that cover the bbox (e.g. ['TQ45'])."""
    minlon, minlat, maxlon, maxlat = bbox
    tr = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    e_min, n_min = tr.transform(minlon, minlat)
    e_max, n_max = tr.transform(maxlon, maxlat)
    tiles: set[str] = set()
    for e in range(int(e_min) // 10000, int(e_max) // 10000 + 1):
        for n in range(int(n_min) // 10000, int(n_max) // 10000 + 1):
            tiles.add(_os_grid_label(e * 10000, n * 10000))
    return sorted(t for t in tiles if t)


def _os_grid_label(easting: int, northing: int) -> str:
    """OS Grid 100km letter pair + 10km digits."""
    if easting < 0 or northing < 0 or easting >= 700000 or northing >= 1300000:
        return ""
    e100, n100 = easting // 100000, northing // 100000
    e500, n500 = e100 // 5, n100 // 5
    e_in, n_in = e100 % 5, n100 % 5
    first_row = 3 - n500
    first_col = e500 + 2
    if not (0 <= first_row < 5 and 0 <= first_col < 5):
        return ""
    first_idx = first_row * 5 + first_col
    second_idx = (4 - n_in) * 5 + e_in
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # I omitted
    first = letters[first_idx]
    second = letters[second_idx]
    e10 = (easting % 100000) // 10000
    n10 = (northing % 100000) // 10000
    return f"{first}{second}{e10}{n10}"


def _uk_resolve_product_guid(tile: str, timeout: int = 30) -> Optional[str]:
    """Hit the geostore catalog for an OS-10km tile and return the GUID for
    the LIDAR Composite DTM 1m product, or None if not found.

    NOTE: geostore.com appears geo-restricted — this call fails from non-UK
    networks with a connect timeout. Run from a UK-routed machine.
    """
    url = GEOSTORE_CATALOG.format(tile=tile)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # Expected shape: list of product dicts with name/guid/resolution.
    products = data if isinstance(data, list) else data.get("products", [])
    for p in products:
        name = (p.get("pyramid") or p.get("name") or "").lower()
        res = str(p.get("resolution") or "").lower()
        if "lidar composite dtm" in name and "1m" in res:
            return p.get("guid") or p.get("id")
    return None


def extract_uk_portal_zip(zip_path: Path, dest_root: Path) -> dict:
    """Extract a DEFRA Survey portal LIDAR Composite DTM 1m zip.

    Portal zips contain a single 5km tile, e.g.
    ``lidar_composite_dtm-2022-1-TQ44sw.zip`` -> ``TQ44sw_DTM_1m.tif`` (plus
    a .tfw world file and .xml metadata at the zip root). Places each .tif at
    ``<dest_root>/uk-1m/<parent_TQ>/<TQ_subtile>_DTM_1m.tif`` so LocalDEM
    picks it up via the existing recursive glob. Idempotent: skips files
    whose target already exists.
    """
    import re
    import shutil
    import zipfile

    out = {"source": Path(zip_path).name, "written": [], "skipped": [], "failed": []}
    with zipfile.ZipFile(zip_path) as z:
        tifs = [
            n for n in z.namelist()
            if n.lower().endswith(".tif") and "_DTM_" in n
        ]
        for member in tifs:
            leaf = Path(member).name
            m = re.match(r"([A-Z]{2}\d{2}[a-z]{0,2})_", leaf)
            if not m:
                out["failed"].append(leaf)
                continue
            subtile = m.group(1)
            parent = subtile[:4]  # e.g. 'TQ44'
            target_dir = dest_root / "uk-1m" / parent
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / leaf
            if target.exists() and target.stat().st_size > 0:
                out["skipped"].append(target.name)
                continue
            with z.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
            out["written"].append(target.name)
    return out


def _move_zip_to_cache(zip_path: Path, dest_root: Path) -> Path | None:
    """Move a processed portal zip out of Downloads into the persistent cache.

    Downloads directories get cleared periodically; the cache survives so we
    can re-extract without re-fetching from the portal. Returns the new path
    or None if move failed (zip stays where it was — never destroyed).
    """
    import shutil
    cache_dir = dest_root / ".cache" / "uk-portal"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / Path(zip_path).name
    if target.exists() and target.stat().st_size == Path(zip_path).stat().st_size:
        # Already cached identically — just delete the duplicate in Downloads.
        try:
            Path(zip_path).unlink()
            return target
        except Exception:
            return None
    try:
        return Path(shutil.move(str(zip_path), str(target)))
    except Exception:
        return None


def extract_uk_portal_dir(archive_dir: Path, dest_root: Path) -> dict:
    """Process every LIDAR Composite DTM zip in a directory.

    After each zip is successfully extracted, the original is moved out of
    `archive_dir` into `<dest_root>/.cache/uk-portal/` so a cleared Downloads
    folder does not lose data. Matches both the `lidar_composite_dtm` prefix
    used by the DEFRA Survey portal and the `national_lidar_programme_dtm`
    prefix used for the newer programme — we only accept DTM (terrain), not
    DSM (surface, includes tree canopy / buildings — wrong for cycling).
    """
    archive_dir = Path(archive_dir)
    zips = sorted(list(archive_dir.glob("lidar_composite_dtm*.zip"))
                  + list(archive_dir.glob("national_lidar_programme_dtm*.zip")))
    if not zips:
        return {"failed": f"no LIDAR DTM zips in {archive_dir}"}
    results = []
    moved: list[str] = []
    move_failed: list[str] = []
    for z in zips:
        r = extract_uk_portal_zip(z, dest_root)
        # Only move if the zip's tile is either freshly written or already
        # present in the dest (not if extraction failed mid-flight).
        if not r.get("failed"):
            new_path = _move_zip_to_cache(z, dest_root)
            if new_path is not None:
                moved.append(z.name)
            else:
                move_failed.append(z.name)
        results.append(r)
    return {
        "archives_processed": len(results),
        "written": sum((r.get("written", []) for r in results), []),
        "skipped": sum((r.get("skipped", []) for r in results), []),
        "failed": sum((r.get("failed", []) for r in results), []),
        "moved_to_cache": moved,
        "move_failed": move_failed,
        "per_archive": results,
    }


def fetch_uk_tile(tile: str, dest_root: Path) -> dict:
    """Download + extract the LIDAR Composite DTM 1m zip for one OS-10km tile.

    Returns {"skipped": bool, "files": [<extracted .tif paths>]} or
    {"failed": str} on error. Idempotent: skips if any .tif already exists
    under <dest_root>/uk-1m/<tile>/.
    """
    out_dir = dest_root / "uk-1m" / tile
    if out_dir.exists() and any(out_dir.glob("*.tif")):
        return {"skipped": True, "tile": tile}
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        guid = _uk_resolve_product_guid(tile)
        if not guid:
            return {"failed": tile, "reason": "no DTM 1m product"}
        zip_path = out_dir / f"{tile}.zip"
        _stream_download(GEOSTORE_DOWNLOAD.format(guid=guid), zip_path)
        # Extract .tif members directly into out_dir.
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            tifs = [n for n in z.namelist() if n.lower().endswith(".tif")]
            for n in tifs:
                z.extract(n, out_dir)
        zip_path.unlink()
        # Flatten any nested folder layout zip may have produced.
        for tif in out_dir.rglob("*.tif"):
            if tif.parent != out_dir:
                tif.rename(out_dir / tif.name)
        return {"tile": tile, "files": sorted(p.name for p in out_dir.glob("*.tif"))}
    except Exception as e:
        return {"failed": tile, "reason": str(e)[:120]}


# ===========================================================================
# FR — IGN Géoplateforme
# ===========================================================================

# Approximate metro-France department bboxes for dept-from-bbox lookup. Only
# the depts we care about for cycling are listed — extend as needed.
_DEPT_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "D075": (2.22, 48.81, 2.47, 48.91),     # Paris
    "D077": (2.40, 48.10, 3.55, 49.13),     # Seine-et-Marne (incl. Fontainebleau)
    "D078": (1.45, 48.65, 2.20, 49.10),     # Yvelines
    "D091": (1.93, 48.30, 2.59, 48.78),     # Essonne
    "D092": (2.07, 48.79, 2.34, 48.95),     # Hauts-de-Seine
    "D093": (2.30, 48.81, 2.65, 48.97),     # Seine-Saint-Denis
    "D094": (2.31, 48.68, 2.62, 48.85),     # Val-de-Marne
    "D095": (1.94, 48.92, 2.61, 49.24),     # Val-d'Oise
}


def dept_for_bbox(bbox: tuple[float, float, float, float]) -> list[str]:
    """Return department codes whose bbox intersects the given bbox.

    Lookup is local (no API call). Returns [] if no known dept matches —
    caller should pass --dept explicitly.
    """
    minlon, minlat, maxlon, maxlat = bbox
    out: list[str] = []
    for code, (lo1, la1, lo2, la2) in _DEPT_BBOXES.items():
        if not (maxlon < lo1 or lo2 < minlon or maxlat < la1 or la2 < minlat):
            out.append(code)
    return out


def resolve_ign_archive_url(dept_code: str, max_pages: int = 25) -> str:
    """Walk the RGE ALTI Atom feed until a dated archive for `dept_code`
    appears. Returns the full HTTPS download URL.

    Atom is paginated; D077 currently lives on page 8, D075 on page 7, etc.
    Caller can cache the result — date suffixes change rarely.
    """
    pat = re.compile(
        rf"(RGEALTI_2-0_1M_ASC_LAMB93-IGN69_{re.escape(dept_code)}_\d{{4}}-\d{{2}}-\d{{2}})"
    )
    for page in range(1, max_pages + 1):
        r = requests.get(f"{IGN_ATOM}?page={page}", timeout=30)
        r.raise_for_status()
        matches = pat.findall(r.text)
        if matches:
            stem = sorted(set(matches))[-1]  # newest date
            return f"{IGN_DOWNLOAD}/{stem}/{stem}.7z"
    raise FileNotFoundError(f"No RGE ALTI archive found for {dept_code}")


def asc_keys_for_bbox(bbox: tuple[float, float, float, float]) -> set[tuple[int, int]]:
    """Return the set of (east_km, north_km) keys for 1km tiles intersecting
    the bbox. IGN .asc filenames embed these as zero-padded km values.
    """
    minlon, minlat, maxlon, maxlat = bbox
    tr = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    pts = [(minlon, minlat), (maxlon, minlat),
           (minlon, maxlat), (maxlon, maxlat)]
    es, ns = zip(*[tr.transform(*p) for p in pts])
    e_min_km = int(min(es)) // 1000
    e_max_km = int(max(es)) // 1000
    n_min_km = int(min(ns)) // 1000
    n_max_km = int(max(ns)) // 1000
    keys: set[tuple[int, int]] = set()
    # IGN tile filename convention: NW corner. So a tile labelled (E_km, N_km)
    # covers [E_km*1000, (E_km+1)*1000] east and [(N_km-1)*1000, N_km*1000] north.
    # Generate the union of labels that could intersect the bbox.
    for e in range(e_min_km, e_max_km + 1):
        for n in range(n_min_km, n_max_km + 2):  # +1 to account for NW-corner labelling
            keys.add((e, n))
    return keys


def _asc_filename_matches(name: str, wanted: set[tuple[int, int]]) -> bool:
    """Match .asc filenames like RGEALTI_FXX_0660_6850_MNT_LAMB93_IGN69.asc."""
    m = re.search(r"_(\d{4})_(\d{4})_MNT_LAMB93", Path(name).name)
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) in wanted


def _asc_to_tif(asc_path: Path, tif_path: Path) -> None:
    """Convert ESRI ASCII grid to LZW-compressed tiled GeoTIFF (lossless).

    IGN RGE ALTI archives ship a single global dalles.prj for the whole
    package, not per-tile sidecars, so rasterio cannot auto-detect the
    CRS from the .asc. Force EPSG:2154 (Lambert-93 / IGN69 vertical) on
    write since that is the documented CRS of the product.
    """
    import rasterio
    from rasterio.crs import CRS
    with rasterio.open(asc_path) as src:
        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            crs=CRS.from_epsg(2154),
            compress="LZW",
            tiled=True,
            blockxsize=256,
            blockysize=256,
            predictor=3,  # float predictor — best ratio for elevation data
        )
        data = src.read(1)
        tif_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(tif_path, "w", **profile) as dst:
            dst.write(data, 1)


def extract_and_convert_fr(
    archive_path: Path,
    bbox: tuple[float, float, float, float],
    dept_code: str,
    dest_root: Path,
    keep_archive: bool = True,
) -> dict:
    """Extract bbox-relevant .asc tiles from a D??? 7z and convert to .tif.

    Writes to <dest_root>/fr-1m/<dept_code>/<stem>.tif. Returns a result
    dict with the tile filenames written, skipped, and any failures.
    """
    import py7zr
    out_dir = dest_root / "fr-1m" / dept_code
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = asc_keys_for_bbox(bbox)
    written: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    with py7zr.SevenZipFile(archive_path, "r") as z:
        all_names = z.getnames()
        targets = [n for n in all_names if _asc_filename_matches(n, wanted)]
        if not targets:
            return {"failed": "no .asc inside archive matched bbox keys",
                    "scanned": len(all_names)}
        # Skip those whose .tif output already exists.
        tmp_dir = dest_root / ".tmp-extract" / dept_code
        tmp_dir.mkdir(parents=True, exist_ok=True)
        targets_to_extract = []
        for t in targets:
            stem = Path(t).stem
            tif_path = out_dir / f"{stem}.tif"
            if tif_path.exists() and tif_path.stat().st_size > 0:
                skipped.append(tif_path.name)
            else:
                targets_to_extract.append(t)
        if targets_to_extract:
            print(
                f"  extracting {len(targets_to_extract)} .asc tile(s) "
                f"from {archive_path.name} ...",
                file=sys.stderr,
            )
            z.extract(path=str(tmp_dir), targets=targets_to_extract)
            for t in targets_to_extract:
                # py7zr preserves the archive's directory structure.
                src = tmp_dir / t
                if not src.exists():
                    failed.append(t)
                    continue
                stem = Path(t).stem
                tif = out_dir / f"{stem}.tif"
                try:
                    _asc_to_tif(src, tif)
                    written.append(tif.name)
                except Exception as e:
                    failed.append(f"{t}: {e}")
        # Cleanup the temp extract dir.
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if not keep_archive:
        archive_path.unlink(missing_ok=True)
    return {
        "dept": dept_code,
        "written": written,
        "skipped": skipped,
        "failed": failed,
    }


def fetch_fr_dept(
    dept_code: str,
    bbox: tuple[float, float, float, float],
    dest_root: Path,
    archive_cache: Optional[Path] = None,
    keep_archive: bool = True,
) -> dict:
    """Top-level FR per-dept fetch: resolve URL → download → extract bbox tiles.

    archive_cache: directory to keep the .7z in (defaults to <dest_root>/.cache).
    """
    cache_dir = archive_cache or (dest_root / ".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = resolve_ign_archive_url(dept_code)
    archive = cache_dir / Path(url).name
    if not archive.exists() or archive.stat().st_size == 0:
        print(f"  downloading {url}", file=sys.stderr)
        _stream_download(url, archive)
    else:
        print(f"  reusing cached {archive.name}", file=sys.stderr)
    return extract_and_convert_fr(
        archive, bbox, dept_code, dest_root, keep_archive=keep_archive
    )


# ===========================================================================
# Back-compat shims (analyse_gpx imports these names)
# ===========================================================================

def ign_tiles_for_bbox(bbox) -> list[str]:
    """Return department codes whose bbox intersects the given bbox.

    Replaces the old per-1km-tile output, since FR now distributes per-dept.
    """
    return dept_for_bbox(bbox)


def fetch_tiles(
    tile_ids: Iterable[str],
    region: str,
    dest_root: Path,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> dict:
    """Dispatch to UK or FR. `tile_ids` are OS-10km labels for UK or
    department codes (D077, etc.) for FR.

    For FR, `bbox` is required to filter which .asc tiles to extract from
    the per-dept archive.
    """
    dest_root = Path(dest_root)
    if region == "uk":
        results = [fetch_uk_tile(t, dest_root) for t in tile_ids]
        downloaded = [r.get("tile") for r in results if "files" in r]
        skipped = [r.get("tile") for r in results if r.get("skipped")]
        failed = [r.get("failed") for r in results if "failed" in r]
        _update_coverage(dest_root, region, downloaded)
        return {"skipped": skipped, "downloaded": downloaded, "failed": failed}
    if region == "fr":
        if bbox is None:
            raise ValueError("fetch_tiles(region='fr') requires bbox=")
        results = [fetch_fr_dept(t, bbox, dest_root) for t in tile_ids]
        written = sum((r.get("written", []) for r in results), [])
        skipped = sum((r.get("skipped", []) for r in results), [])
        failed = sum((r.get("failed", []) for r in results), [])
        _update_coverage(dest_root, region, [r.get("dept") for r in results if "dept" in r])
        return {"skipped": skipped, "downloaded": written, "failed": failed}
    raise ValueError(f"unknown region: {region}")


def _update_coverage(dest_root: Path, region: str, new_tiles: list) -> None:
    cov = Path(dest_root) / "coverage.json"
    data = json.loads(cov.read_text()) if cov.exists() else {}
    data.setdefault(region, [])
    for t in new_tiles:
        if t and t not in data[region]:
            data[region].append(t)
    dest_root.mkdir(parents=True, exist_ok=True)
    cov.write_text(json.dumps(data, indent=2))


# ===========================================================================
# CLI
# ===========================================================================

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat")
    src.add_argument("--gpx", type=Path)
    src.add_argument("--region", choices=list(PRESETS))
    src.add_argument(
        "--archive-dir",
        type=Path,
        help="UK: directory of DEFRA portal lidar_composite_dtm*.zip files "
             "(use this when manually downloading via the portal)",
    )
    p.add_argument("--country", choices=["uk", "fr"], default=None)
    p.add_argument("--dept", help="FR only: department code, e.g. D077")
    p.add_argument(
        "--archive",
        type=Path,
        help="FR only: re-use a local .7z instead of downloading",
    )
    p.add_argument(
        "--dest", default=str(Path.home() / "cycling-coach-dem"),
        help="Output root (default: ~/cycling-coach-dem)",
    )
    args = p.parse_args(argv)

    dept = args.dept
    bbox = None
    if args.archive_dir:
        # UK manual-download mode — no bbox/dept needed; just process the zips.
        dest_root = Path(args.dest)
        print(
            f"UK extract-archive mode: scanning {args.archive_dir} -> {dest_root}",
            file=sys.stderr,
        )
        result = extract_uk_portal_dir(args.archive_dir, dest_root)
        print(json.dumps(result, indent=2))
        return
    if args.region:
        country, bbox, preset_dept = PRESETS[args.region]
        dept = dept or preset_dept
    else:
        country = args.country or "uk"
        if args.bbox:
            bbox = tuple(float(x) for x in args.bbox.split(","))
        else:
            bbox = bbox_from_gpx(args.gpx)

    dest_root = Path(args.dest)
    print(f"Country={country}  bbox={bbox}  dest={dest_root}", file=sys.stderr)

    if country == "uk":
        tiles = os_grid_tiles_for_bbox(bbox)
        print(f"UK OS-10km tiles: {tiles}", file=sys.stderr)
        result = fetch_tiles(tiles, region="uk", dest_root=dest_root)
    else:
        if not dept:
            depts = dept_for_bbox(bbox)
            if not depts:
                raise SystemExit(
                    "FR: no preset dept found for bbox; pass --dept D???"
                )
            dept = depts[0]
        if args.archive:
            result = extract_and_convert_fr(
                args.archive, bbox, dept, dest_root, keep_archive=True
            )
        else:
            result = fetch_fr_dept(dept, bbox, dest_root)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
