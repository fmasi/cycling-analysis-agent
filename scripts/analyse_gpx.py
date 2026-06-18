"""
GPX route analyser — pre-ride climb identification and speed predictions.

Usage:
    python scripts/analyse_gpx.py routes/2026-04-25-lost-lanes-18.gpx
    python scripts/analyse_gpx.py routes/file.gpx --json
    python scripts/analyse_gpx.py routes/file.gpx --save

Identifies climbs >300m / >20m gain, predicts speed/duration at FTP/MAP/Z3/Z2,
estimates TSS for a given target intensity, and produces a pacing narrative.
"""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from physics_model import (
    FTP, MAP_WORKING, AC_FRESH_EST, predict_speed, vam_at_power,
    power_for_60rpm_in_lowest_gear, SYSTEM_WEIGHT_KG,
    speed_at_cadence_rpm
)


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * r * math.asin(math.sqrt(a))


def parse_gpx(path):
    """Extract trackpoints from a GPX file."""
    tree = ET.parse(str(path))
    root = tree.getroot()
    ns_uri = root.tag.split('}')[0].strip('{')
    ns = {'gpx': ns_uri}

    trkpts = root.findall('.//gpx:trkpt', ns)
    name_el = root.find('.//gpx:trk/gpx:name', ns)
    track_name = name_el.text if name_el is not None else Path(path).stem

    if not trkpts:
        return None

    lats = np.array([float(p.attrib['lat']) for p in trkpts])
    lons = np.array([float(p.attrib['lon']) for p in trkpts])
    eles = []
    for p in trkpts:
        ele = p.find('gpx:ele', ns)
        eles.append(float(ele.text) if ele is not None else 0)
    eles = np.array(eles)

    # Cumulative distance via haversine
    dists = np.zeros(len(lats))
    for i in range(1, len(lats)):
        dists[i] = dists[i-1] + haversine_m(lats[i-1], lons[i-1], lats[i], lons[i])

    return {
        'name': track_name,
        'lats': lats,
        'lons': lons,
        'eles': eles,
        'dists': dists,
    }


def smooth(arr, w=15):
    """Rolling mean smoother."""
    if len(arr) < w:
        return arr.copy()
    return np.convolve(arr, np.ones(w)/w, mode='same')


def median_filter_1d(arr, size=5):
    """Simple 1-D median filter — removes single-point GPS elevation spikes."""
    if size < 2 or len(arr) < size:
        return arr.copy()
    half = size // 2
    out = np.empty_like(arr, dtype=float)
    n = len(arr)
    for i in range(n):
        s = max(0, i - half)
        e = min(n, i + half + 1)
        out[i] = np.median(arr[s:e])
    return out


def compute_max_grade(dists, eles, start_m, end_m, win_m=50, median_size=5,
                      step_m=10):
    """
    Robust max-grade estimate over a [start_m, end_m] segment.

    Uses median-filtered raw elevation (kills GPS spikes) + 50m grade window
    on a 10m grid. 50m ≈ 15 s on the climb at FTP — short enough to capture
    real ramps you'd feel in your legs, long enough to dodge single-point noise.
    """
    pad = max(win_m, 100)
    mask = (dists >= start_m - pad) & (dists <= end_m + pad)
    if mask.sum() < 5:
        return 0.0
    e_filt = median_filter_1d(eles[mask], size=median_size)
    grid = np.arange(dists[mask][0], dists[mask][-1], step_m)
    if len(grid) < 4:
        return 0.0
    eg = np.interp(grid, dists[mask], e_filt)
    half = max(1, int(win_m / step_m / 2))
    if len(eg) <= 2 * half:
        return 0.0
    grad = np.zeros_like(eg)
    grad[half:-half] = (eg[2*half:] - eg[:-2*half]) / (2 * half * step_m) * 100
    in_seg = (grid >= start_m) & (grid <= end_m)
    if not in_seg.any():
        return 0.0
    return float(grad[in_seg].max())


def find_climbs(dists, eles, min_length_m=300, min_gain_m=20, min_grade=0.015):
    """
    Find climbs in a route.

    Detection uses a 200m-window grade on smoothed elevation (appropriate for
    "what counts as a sustained climb"). max_grad_pct is then recomputed with
    a 50m window on median-filtered raw elevation to give a true max that
    matches what you'd feel on the road, without GPS-noise inflation.
    """
    if len(dists) < 50:
        return []

    ele_s = smooth(eles, 15)
    max_d = dists[-1]
    if max_d < 100:
        return []

    d_grid = np.arange(0, max_d, 50)
    alt_d = np.interp(d_grid, dists, ele_s)

    window_n = 4  # 200m at 50m grid — climb DETECTION window
    if len(alt_d) <= window_n:
        return []
    grad = (alt_d[window_n:] - alt_d[:-window_n]) / 200
    grad = np.concatenate([np.zeros(window_n // 2), grad,
                           np.zeros(window_n - window_n // 2)])

    in_climb = grad > min_grade
    climbs = []
    i = 0
    while i < len(in_climb):
        if in_climb[i]:
            start = i
            j = i
            while j < len(in_climb) and (in_climb[j] or
                  (j + 4 < len(in_climb) and in_climb[j:j+4].any())):
                j += 1
            length_m = (j - start) * 50
            gain = alt_d[min(j, len(alt_d)-1)] - alt_d[start]
            if length_m >= min_length_m and gain >= min_gain_m:
                start_m = float(d_grid[start])
                end_m = float(d_grid[min(j, len(d_grid)-1)])
                max_grad = compute_max_grade(dists, eles, start_m, end_m)
                climbs.append({
                    'start_km': start_m / 1000,
                    'end_km': end_m / 1000,
                    'length_m': float(length_m),
                    'gain_m': float(gain),
                    'avg_grad_pct': float(gain / length_m * 100),
                    'max_grad_pct': max_grad,
                })
            i = j
        else:
            i += 1
    return climbs


def predict_climb(climb):
    """Compute speed/duration/VAM/gear info for a single climb."""
    grad = climb['avg_grad_pct']
    length_km = climb['length_m'] / 1000
    out = {}

    # Power targets derived from the profile (never hardcode FTP/MAP into keys
    # or labels — they desync the moment the rider re-tests). Stable keys
    # ('ftp','map','z3','z2'); the watts + display label travel with the value.
    z3_w, z2_w = round(0.80 * FTP), round(0.65 * FTP)
    out['powers'] = {}
    for key, power, label in [
        ('ftp', FTP, f'FTP ({FTP}W)'),
        ('map', MAP_WORKING, f'MAP ({MAP_WORKING}W)'),
        ('z3', z3_w, f'Z3 ({z3_w}W)'),
        ('z2', z2_w, f'Z2 ({z2_w}W)'),
    ]:
        speed = predict_speed(power, grad)
        out['powers'][key] = {
            'w': power,
            'label': label,
            'speed_kmh': round(speed, 2),
            'time_min': round(length_km / speed * 60, 1) if speed > 0 else None,
        }

    out['vam_at_ftp_mh'] = round(vam_at_power(FTP, grad), 0)
    # Survival check at max grade
    if climb['max_grad_pct'] > 5:
        out['power_for_60rpm_at_max_grad_w'] = round(
            power_for_60rpm_in_lowest_gear(climb['max_grad_pct']), 0)

    # Pacing intent based on duration — bounds derived from FTP/MAP/AC.
    time_at_ftp = length_km / predict_speed(FTP, grad) * 60
    if time_at_ftp < 3:
        intent = f'AC zone (sub-3min) — push hard up to ~{AC_FRESH_EST}W'
    elif time_at_ftp <= 8:
        intent = (f'MAP zone (3-8min) — primary development zone, '
                  f'target {FTP}-{MAP_WORKING}W')
    elif time_at_ftp <= 20:
        intent = f'Threshold to Sweet Spot (8-20min) — {round(0.85 * FTP)}-{FTP}W'
    else:
        intent = (f'Sweet Spot (20+ min) — {round(0.85 * FTP)}-{round(0.94 * FTP)}W '
                  f'(85-94% FTP)')
    out['recommended_intent'] = intent

    return out


def estimate_tss(distance_km, climbs, target_if=0.65):
    """
    Rough TSS estimate for a planned ride.

    Assumes ~25 km/h average on flat sections, climbs computed at predicted speed,
    target IF chosen by rider.
    """
    if not climbs:
        flat_km = distance_km
        climb_min = 0
    else:
        climb_km = sum(c['length_m'] for c in climbs) / 1000
        # Climb lengths come off the 50 m detection grid and aren't clamped to
        # route distance; on climb-dense routes their sum can exceed it. Clamp
        # so flat_km can't go negative (which would understate time and TSS).
        flat_km = max(0.0, distance_km - climb_km)
        # Climbs done at ~75% FTP avg → speed depends on grade
        climb_min = sum(
            (c['length_m'] / 1000) / predict_speed(0.75 * FTP, c['avg_grad_pct']) * 60
            for c in climbs
        )

    # Flat sections at 25 km/h
    flat_min = flat_km / 25 * 60
    total_hours = (flat_min + climb_min) / 60
    tss = total_hours * (target_if ** 2) * 100
    return {
        'estimated_total_hours': round(total_hours, 2),
        'estimated_tss_at_if_065': round(tss, 0),
        'estimated_tss_at_if_070': round(total_hours * 0.70**2 * 100, 0),
        'estimated_tss_at_if_075': round(total_hours * 0.75**2 * 100, 0),
    }


def analyse(path, include_coords=False):
    data = parse_gpx(path)
    if data is None:
        return {'file': str(path), 'error': 'No trackpoints found'}

    dists = data['dists']
    eles = data['eles']
    lats = data['lats']
    lons = data['lons']

    total_ascent = float(np.sum(np.maximum(0, np.diff(smooth(eles, 9)))))
    total_descent = float(np.sum(np.maximum(0, -np.diff(smooth(eles, 9)))))

    climbs = find_climbs(dists, eles)

    # Add per-climb coordinates and trackpoints if requested (for JSON regression tests)
    if include_coords:
        for c in climbs:
            start_m, end_m = c['start_km'] * 1000, c['end_km'] * 1000
            idx = np.where((dists >= start_m) & (dists <= end_m))[0]
            c['coords'] = [
                {'lat': float(lats[i]), 'lon': float(lons[i])}
                for i in idx
            ]

    for c in climbs:
        c['predictions'] = predict_climb(c)

    # Same start/end?
    is_loop = (abs(lats[0] - lats[-1]) < 0.001 and
               abs(lons[0] - lons[-1]) < 0.001)

    result = {
        'file': str(path),
        'route_name': data['name'],
        'distance_km': round(float(dists[-1] / 1000), 2),
        'total_ascent_m': round(total_ascent, 0),
        'total_descent_m': round(total_descent, 0),
        'min_elevation_m': round(float(eles.min()), 0),
        'max_elevation_m': round(float(eles.max()), 0),
        'start_lat': round(float(lats[0]), 5),
        'start_lon': round(float(lons[0]), 5),
        'is_loop': is_loop,
        'climbs': climbs,
        'tss_estimate': estimate_tss(dists[-1] / 1000, climbs),
    }

    # Add full trackpoints if requested (for regression tests)
    if include_coords:
        result['trackpoints'] = [
            {'lat': float(lats[i]), 'lon': float(lons[i]), 'ele': float(eles[i]), 'cum_m': float(dists[i])}
            for i in range(len(lats))
        ]

    return result


def format_markdown(r):
    """Render analysis as markdown for routes/."""
    lines = []
    lines.append(f"# Route — {r.get('route_name', 'Unknown')}\n")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"Source: `{r['file']}`\n")
    lines.append("\n## Summary\n")
    lines.append(f"- **Distance**: {r['distance_km']} km")
    lines.append(f"- **Ascent**: {r['total_ascent_m']:.0f} m")
    lines.append(f"- **Descent**: {r['total_descent_m']:.0f} m")
    lines.append(f"- **Elevation range**: {r['min_elevation_m']:.0f} – {r['max_elevation_m']:.0f} m")
    lines.append(f"- **Loop**: {'yes' if r['is_loop'] else 'no'}")
    lines.append(f"- **Start**: ({r['start_lat']}, {r['start_lon']})")

    te = r['tss_estimate']
    lines.append("\n## TSS estimate\n")
    lines.append(f"- **Total moving time**: ~{te['estimated_total_hours']} h")
    lines.append(f"- **TSS at IF 0.65** (easy social): ~{te['estimated_tss_at_if_065']:.0f}")
    lines.append(f"- **TSS at IF 0.70** (moderate endurance): ~{te['estimated_tss_at_if_070']:.0f}")
    lines.append(f"- **TSS at IF 0.75** (firm endurance): ~{te['estimated_tss_at_if_075']:.0f}")

    if r['climbs']:
        lines.append(f"\n## Climbs ({len(r['climbs'])})\n")
        for i, c in enumerate(r['climbs'], 1):
            p = c['predictions']
            lines.append(f"### Climb {i}: km {c['start_km']:.2f} – {c['end_km']:.2f}\n")
            lines.append(f"- **Length**: {c['length_m']:.0f} m | "
                         f"**Gain**: {c['gain_m']:.0f} m | "
                         f"**Avg grade**: {c['avg_grad_pct']:.1f}% | "
                         f"**Max**: {c['max_grad_pct']:.1f}%")
            lines.append("<!-- BEGIN GPX-PACING -->")
            for key in ('ftp', 'map', 'z3'):
                pw = p['powers'][key]
                lines.append(f"- **Speed @ {pw['label']}**: {pw['speed_kmh']} km/h "
                             f"(~{pw['time_min']} min)")
            lines.append(f"- **VAM at FTP**: {p['vam_at_ftp_mh']:.0f} m/h")
            if 'power_for_60rpm_at_max_grad_w' in p:
                lines.append(f"- **Survival (60rpm in 30×32 at max grade)**: "
                             f"{p['power_for_60rpm_at_max_grad_w']} W")
            lines.append(f"- **Pacing**: {p['recommended_intent']}")
            lines.append("<!-- END GPX-PACING -->")
            lines.append("")

    return '\n'.join(lines) + '\n'


def render_overview_chart(gpx_path, climbs, include=(), exclude=(),
                           print_inventory=True, dists=None, elevs=None,
                           data_source=None, walls=None):
    """Generate the TdF-style overview PNG.

    Always shows the full waypoint inventory (with keep/drop decisions and
    reasons). Override defaults via include/exclude index lists.

    If `dists` and `elevs` are supplied (e.g. hi-fi stitched profile from
    the verifier), they replace the GPX-parsed elevation series. Waypoints
    and climb windows still derive from the GPX since they share the same
    cumulative-distance axis.
    """
    from chart_overview import (
        render_overview, enumerate_waypoints, apply_overrides,
        format_waypoint_table, kept_for_render,
    )
    data = parse_gpx(gpx_path)
    if data is None:
        return None

    items = enumerate_waypoints(gpx_path, data['lats'], data['lons'], data['dists'])
    items = apply_overrides(items, include=include, exclude=exclude)

    if print_inventory:
        print('\n=== Waypoints found in GPX ===', file=sys.stderr)
        print(format_waypoint_table(items), file=sys.stderr)

    stem = Path(gpx_path).stem
    out_dir = Path(__file__).parent.parent / 'rides' / 'charts'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{stem}-overview.png'

    import numpy as np
    use_dists = np.asarray(dists if dists is not None else data['dists'])
    use_elevs = np.asarray(elevs if elevs is not None else data['eles'])

    render_overview(
        use_dists, use_elevs, climbs, kept_for_render(items),
        str(out_path), title=data['name'], data_source=data_source,
        walls=walls)
    return out_path


def _parse_idx_list(s):
    if not s:
        return []
    return [int(x.strip()) for x in s.split(',') if x.strip()]


def main():
    parser = argparse.ArgumentParser(description='Analyse GPX routes for cycling.')
    parser.add_argument('files', nargs='+', help='GPX file path(s)')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    parser.add_argument('--save', action='store_true',
                        help='Save markdown to routes/<n>-prediction.md '
                             'and overview chart to rides/charts/<n>-overview.png')
    parser.add_argument('--no-chart', action='store_true',
                        help='Skip chart generation when --save is set')
    parser.add_argument('--gpx-only-chart', action='store_true',
                        help='Render the overview chart from raw GPX elevations '
                             'only, ignoring the hi-fi stitched profile. '
                             'Use for A/B sanity checks vs the legacy chart.')
    parser.add_argument('--include', default='',
                        help='Comma-separated waypoint indices to force-include '
                             '(overrides auto-drop). Example: --include 4,7')
    parser.add_argument('--exclude', default='',
                        help='Comma-separated waypoint indices to force-exclude. '
                             'Example: --exclude 8')
    parser.add_argument('--list-waypoints', action='store_true',
                        help='Just print the waypoint inventory and exit (no chart, no markdown)')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip Fidelity Report generation against on-device DEM.')
    parser.add_argument('--coverage-gap',
                        choices=['download', 'api', 'skip', 'fail'],
                        default=None,
                        help="Policy when route extends outside loaded DEM tiles. "
                             "Defaults: 'download' (interactive), 'api' (non-interactive with key), "
                             "'skip' (non-interactive without key).")
    parser.add_argument('--dem-root',
                        default=str(Path.home() / 'cycling-coach-dem'),
                        help='Path to local DEM tile root.')
    args = parser.parse_args()

    include = _parse_idx_list(args.include)
    exclude = _parse_idx_list(args.exclude)

    for f in args.files:
        # --list-waypoints is a quick inventory mode
        if args.list_waypoints:
            from chart_overview import (
                enumerate_waypoints, apply_overrides, format_waypoint_table)
            data = parse_gpx(f)
            if data is None:
                print(f'No trackpoints in {f}', file=sys.stderr)
                continue
            items = enumerate_waypoints(
                f, data['lats'], data['lons'], data['dists'])
            items = apply_overrides(items, include=include, exclude=exclude)
            print(f'\n=== Waypoints in {Path(f).name} ===')
            print(format_waypoint_table(items))
            continue

        result = analyse(f, include_coords=args.json)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            md = format_markdown(result)
            print(md)
            if args.save:
                stem = Path(f).stem
                out_dir = Path(__file__).parent.parent / 'routes'
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f'{stem}-prediction.md'
                out_path.write_text(md)
                print(f'\n[Saved markdown to {out_path}]', file=sys.stderr)

                # Chart rendering is deferred until after verification so we
                # can feed the hi-fi stitched profile into chart_overview.
                # `report` is set below when verify runs successfully.
                report = None

                if not args.no_verify:
                    try:
                        from local_dem import LocalDEM
                        from elevation_fallback import GPXZClient
                        from verify_climbs import verify_route, embed_in_prediction

                        dem_root = Path(args.dem_root)
                        if not dem_root.exists():
                            print(
                                f'⚠ No DEM tiles found at {dem_root} — '
                                'skipping verification.',
                                file=sys.stderr,
                            )
                        else:
                            dem = LocalDEM(dem_root)
                            fallback = GPXZClient()

                            # Coverage-gap resolution: detect uncovered
                            # coords up-front and apply the configured policy.
                            import sys as _sys
                            from verify_climbs import (
                                resolve_coverage_policy, prompt_coverage_gap,
                            )
                            parsed = parse_gpx(f)
                            aborted = False
                            if parsed is not None:
                                lats = parsed['lats']
                                lons = parsed['lons']
                                uncovered = [
                                    (la, lo)
                                    for la, lo in zip(lats, lons)
                                    if not dem.covers(la, lo)
                                ]
                                if uncovered:
                                    interactive = _sys.stdin.isatty()
                                    has_key = fallback.configured
                                    policy = resolve_coverage_policy(
                                        args.coverage_gap, interactive, has_key,
                                    )
                                    missing: list[str] = []
                                    is_uk = True
                                    if policy in ('prompt', 'download'):
                                        from fetch_dem_tiles import (
                                            os_grid_tiles_for_bbox,
                                            ign_tiles_for_bbox,
                                            fetch_tiles,
                                        )
                                        u_lats = [c[0] for c in uncovered]
                                        u_lons = [c[1] for c in uncovered]
                                        bbox = (
                                            min(u_lons), min(u_lats),
                                            max(u_lons), max(u_lats),
                                        )
                                        is_uk = (
                                            -8 < bbox[0] < 2
                                            and 49 < bbox[1] < 61
                                        )
                                        missing = (
                                            os_grid_tiles_for_bbox(bbox)
                                            if is_uk
                                            else ign_tiles_for_bbox(bbox)
                                        )
                                    if policy == 'prompt':
                                        policy = prompt_coverage_gap(
                                            missing, total_mb=len(missing) * 50,
                                        )
                                    if policy == 'download':
                                        from fetch_dem_tiles import fetch_tiles
                                        kwargs = {} if is_uk else {'bbox': bbox}
                                        fetch_tiles(
                                            missing,
                                            region='uk' if is_uk else 'fr',
                                            dest_root=dem_root,
                                            **kwargs,
                                        )
                                        dem = LocalDEM(dem_root)
                                    elif policy == 'skip':
                                        fallback = None
                                    elif policy == 'quit':
                                        print('Aborted', file=_sys.stderr)
                                        aborted = True
                                    # 'api' / 'fail' — leave fallback as-is

                            if not aborted:
                                report = verify_route(
                                    f, dem, fallback=fallback,
                                )
                                embed_in_prediction(out_path, report)
                                print(
                                    f'  Embedded Fidelity Report '
                                    f'({report.verdict}) in {out_path}',
                                    file=sys.stderr,
                                )
                    except Exception as e:
                        print(f'⚠ Verification failed: {e}', file=sys.stderr)

                # Render the overview chart now — with hi-fi stitched data
                # if the verifier produced any, GPX-only otherwise (or if
                # --gpx-only-chart was passed).
                if not args.no_chart and 'climbs' in result:
                    use_hifi = (
                        report is not None
                        and getattr(report, 'stitched_dists', None)
                        and not args.gpx_only_chart
                    )
                    # Pick badge: hi-fi when stitched data made it through;
                    # gpx-forced when user passed --gpx-only-chart;
                    # gpx-degraded when verifier was supposed to run but
                    # produced no usable stitched output (rate-limited,
                    # offline, no DEM, etc.).
                    if use_hifi:
                        data_source = 'hi-fi'
                    elif args.gpx_only_chart:
                        data_source = 'gpx-forced'
                    elif args.no_verify:
                        data_source = 'gpx-forced'
                    else:
                        data_source = 'gpx-degraded'

                    # Collect walls from the verification report (route-km
                    # absolute = climb start + per-climb offset).
                    walls_for_chart = []
                    if use_hifi:
                        for cv in report.climbs:
                            for w in cv.walls:
                                walls_for_chart.append({
                                    'route_km': cv.km_start + w['offset_m'] / 1000.0,
                                    'peak_pct': w['peak_pct'],
                                    'length_m': w['length_m'],
                                })

                    chart_path = render_overview_chart(
                        f, result['climbs'],
                        include=include, exclude=exclude,
                        dists=(report.stitched_dists if use_hifi else None),
                        elevs=(report.stitched_elevs if use_hifi else None),
                        data_source=data_source,
                        walls=walls_for_chart or None,
                    )
                    if chart_path:
                        print(
                            f'[Saved {data_source} chart to {chart_path}]',
                            file=sys.stderr,
                        )


if __name__ == '__main__':
    main()
