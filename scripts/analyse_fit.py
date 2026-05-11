"""
FIT file analyser — post-ride structured parse.

Usage:
    python scripts/analyse_fit.py rides/<name>.fit
    python scripts/analyse_fit.py rides/*.fit                  # batch
    python scripts/analyse_fit.py rides/<name>.fit --json      # machine-readable
    python scripts/analyse_fit.py rides/<name>.fit --save      # write markdown to rides/analyses/

Reads stored TSS/NP/IF from the FIT file (don't recompute — fitparse rounds NP
which throws off TSS by ~1-2 points). Falls back to recomputation only if
stored values are missing.

Outputs a structured analysis suitable for inclusion in rides/analyses/.

Rider-specific zone bounds, FTP/MAP/AC/NM and weight come from
USER_PROFILE.md via scripts/profile.py.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import fitparse
except ImportError:
    print("ERROR: fitparse not installed. Run: pip install fitparse", file=sys.stderr)
    sys.exit(1)

import numpy as np

# Import physics model and profile from same directory
sys.path.insert(0, str(Path(__file__).parent))
from physics_model import (
    FTP, MAP_WORKING, predict_speed, ZONES,
    SYSTEM_WEIGHT_KG, RIDER_WEIGHT_KG
)
from profile import MAX_HR_BPM, REST_HR_BPM


def parse_fit(path):
    """Extract structured data from a FIT file."""
    fit = fitparse.FitFile(str(path))

    # Session-level stored values (preferred over recomputation)
    session = None
    for msg in fit.get_messages('session'):
        session = {f.name: f.value for f in msg if f.value is not None}
        break  # there's only ever one session

    # Records (1Hz timer-time samples)
    records = []
    for msg in fit.get_messages('record'):
        d = {f.name: f.value for f in msg}
        records.append(d)

    # Laps (for interval workouts)
    laps = []
    for msg in fit.get_messages('lap'):
        d = {f.name: f.value for f in msg if f.value is not None}
        laps.append(d)

    return session, records, laps


def to_arrays(records):
    """Convert record list to aligned numpy arrays."""
    if not records:
        return None
    t0 = records[0]['timestamp']
    out = {
        'time_s': np.array([(r['timestamp'] - t0).total_seconds() for r in records]),
        'distance_m': np.array([r.get('distance', 0) or 0 for r in records]),
        'altitude_m': np.array([r.get('enhanced_altitude', r.get('altitude', 0)) or 0 for r in records]),
        'speed_kmh': np.array([(r.get('enhanced_speed', r.get('speed', 0)) or 0) * 3.6 for r in records]),
        'power_w': np.array([r.get('power', 0) or 0 for r in records]),
        'hr_bpm': np.array([r.get('heart_rate', 0) or 0 for r in records]),
        'cadence_rpm': np.array([r.get('cadence', 0) or 0 for r in records]),
    }
    return out


def power_curve(power_arr, durations_s=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600)):
    """Best-effort power for given durations."""
    out = {}
    for d in durations_s:
        if d == 1:
            out[d] = float(power_arr.max()) if len(power_arr) else 0
            continue
        if d > len(power_arr):
            continue
        cumsum = np.cumsum(power_arr.astype(float))
        rolling = (cumsum[d:] - cumsum[:-d]) / d
        out[d] = float(rolling.max()) if len(rolling) else 0
    return out


def hr_zones_distribution(hr_arr, max_hr=MAX_HR_BPM, rest_hr=REST_HR_BPM):
    """Time in each Karvonen HR zone (seconds)."""
    valid = hr_arr[hr_arr > 0]
    if len(valid) == 0:
        return {}
    # Karvonen: HR_target = (max - rest) * pct + rest
    bounds = {
        'Z1 Recovery': (0.50, 0.60),
        'Z2 Aerobic': (0.60, 0.70),
        'Z3 Tempo': (0.70, 0.80),
        'Z4 Threshold': (0.80, 0.90),
        'Z5 VO2max': (0.90, 1.00),
    }
    hrr = max_hr - rest_hr
    out = {}
    for name, (lo, hi) in bounds.items():
        bpm_lo = rest_hr + hrr * lo
        bpm_hi = rest_hr + hrr * hi
        if name == 'Z5 VO2max':
            count = ((valid >= bpm_lo) & (valid <= max_hr + 10)).sum()
        else:
            count = ((valid >= bpm_lo) & (valid < bpm_hi)).sum()
        out[name] = int(count)
    return out


def power_zones_distribution(power_arr, cadence_arr):
    """Time in each power zone (pedalling only, seconds).

    Zone bounds come from `physics_model.ZONES`, which materialise from the
    profile's FTP / MAP / AC / NM.
    """
    pedalling = cadence_arr > 0
    valid = power_arr[pedalling]
    if len(valid) == 0:
        return {}
    out = {}
    # ZONES is a list of (name, lo, hi); collapse adjacent overlapping zones
    # (Z4 Sweet Spot and Z5 Threshold overlap by design — combine them for
    # the time-in-zone summary).
    summary_bounds = [
        ('Z1 Recovery',         ZONES[0][1], ZONES[0][2]),
        ('Z2 Endurance',        ZONES[1][1], ZONES[1][2]),
        ('Z3 Tempo',            ZONES[2][1], ZONES[2][2]),
        ('Z4-Z5 SS/Threshold',  ZONES[3][1], ZONES[4][2]),   # Sweet Spot lo → Threshold hi
        ('Z6 MAP',              ZONES[5][1], ZONES[5][2]),
        ('Z7 AC',               ZONES[6][1], ZONES[6][2]),
        ('Z8 NM',               ZONES[7][1], 9999),
    ]
    for name, lo, hi in summary_bounds:
        count = ((valid >= lo) & (valid <= hi)).sum()
        out[name] = int(count)
    return out


def median_filter_1d(arr, size=5):
    """Simple 1-D median filter — removes single-point GPS elevation spikes."""
    if size < 2 or len(arr) < size:
        return np.array(arr, dtype=float)
    half = size // 2
    out = np.empty(len(arr), dtype=float)
    n = len(arr)
    for i in range(n):
        s = max(0, i - half)
        e = min(n, i + half + 1)
        out[i] = np.median(arr[s:e])
    return out


def compute_max_grade(distance_m, altitude_m, start_m, end_m,
                      win_m=50, median_size=5, step_m=10):
    """
    Robust max-grade estimate over a [start_m, end_m] segment.

    Uses median-filtered raw elevation (kills GPS spikes) + 50m grade window
    on a 10m grid. 50m ≈ 15 s on the climb at FTP — short enough to capture
    real ramps you'd feel in your legs, long enough to dodge single-point noise.
    """
    distance_m = np.asarray(distance_m)
    altitude_m = np.asarray(altitude_m)
    pad = max(win_m, 100)
    mask = (distance_m >= start_m - pad) & (distance_m <= end_m + pad)
    if mask.sum() < 5:
        return 0.0
    e_filt = median_filter_1d(altitude_m[mask], size=median_size)
    d_in = distance_m[mask]
    grid = np.arange(d_in[0], d_in[-1], step_m)
    if len(grid) < 4:
        return 0.0
    eg = np.interp(grid, d_in, e_filt)
    half = max(1, int(win_m / step_m / 2))
    if len(eg) <= 2 * half:
        return 0.0
    grad = np.zeros_like(eg)
    grad[half:-half] = (eg[2*half:] - eg[:-2*half]) / (2 * half * step_m) * 100
    in_seg = (grid >= start_m) & (grid <= end_m)
    if not in_seg.any():
        return 0.0
    return float(grad[in_seg].max())


def find_climbs(distance_m, altitude_m, min_length_m=300, min_gain_m=20,
                min_grade=0.015):
    """
    Identify sustained climbs along a ride.

    Detection uses 200m rolling gradient on smoothed altitude (appropriate for
    "what counts as a sustained climb"). max_grad_pct is then recomputed with
    a 50m window on median-filtered raw elevation to give a true max that
    matches what you'd feel on the road, without GPS-noise inflation.
    """
    if len(distance_m) < 50:
        return []

    # Smooth altitude (climb DETECTION only — not used for max grade)
    win = min(15, len(altitude_m))
    alt_s = np.convolve(altitude_m, np.ones(win)/win, mode='same')

    # Resample on 50m grid
    max_d = distance_m[-1]
    if max_d < 100:
        return []
    d_grid = np.arange(0, max_d, 50)
    alt_d = np.interp(d_grid, distance_m, alt_s)

    # 200m rolling gradient — detection window
    window_n = 4
    if len(alt_d) <= window_n:
        return []
    grad = (alt_d[window_n:] - alt_d[:-window_n]) / 200
    grad = np.concatenate([np.zeros(window_n // 2), grad,
                           np.zeros(window_n - window_n // 2)])

    # Find sustained climb segments
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
                max_grad = compute_max_grade(distance_m, altitude_m,
                                             start_m, end_m)
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


def analyse(path):
    """Top-level analysis. Returns a dict suitable for JSON or markdown."""
    session, records, laps = parse_fit(path)
    arrays = to_arrays(records)

    # Pull stored values if available — DON'T recompute
    timer_time_s = session.get('total_timer_time', 0) if session else 0
    elapsed_time_s = session.get('total_elapsed_time', 0) if session else 0

    result = {
        'file': str(path),
        'start_time': str(session.get('start_time', '')) if session else '',
        'sport': session.get('sport', '') if session else '',
        'sub_sport': session.get('sub_sport', '') if session else '',
        # Stored values — preferred
        'distance_km': float(session.get('total_distance', 0) / 1000) if session else 0,
        'timer_time_s': float(timer_time_s),
        'elapsed_time_s': float(elapsed_time_s),
        'auto_pause_s': float(elapsed_time_s - timer_time_s),
        'tss': float(session.get('training_stress_score', 0)) if session else 0,
        'normalized_power_w': float(session.get('normalized_power', 0)) if session else 0,
        'intensity_factor': float(session.get('intensity_factor', 0)) if session else 0,
        'avg_power_w': float(session.get('avg_power', 0)) if session else 0,
        'max_power_w': float(session.get('max_power', 0)) if session else 0,
        'avg_hr_bpm': float(session.get('avg_heart_rate', 0)) if session else 0,
        'max_hr_bpm': float(session.get('max_heart_rate', 0)) if session else 0,
        'avg_cadence_rpm': float(session.get('avg_cadence', 0)) if session else 0,
        'total_ascent_m': float(session.get('total_ascent', 0)) if session else 0,
        'total_descent_m': float(session.get('total_descent', 0)) if session else 0,
        'threshold_power_used_w': float(session.get('threshold_power', 0)) if session else 0,
        'num_laps': int(session.get('num_laps', 0)) if session else 0,
    }

    if arrays is None or len(arrays['time_s']) < 10:
        result['note'] = 'Insufficient records to compute curves/zones'
        return result

    # Power curve
    if (arrays['power_w'] > 0).any():
        pc = power_curve(arrays['power_w'])
        result['power_curve'] = {f'{k}s': round(v, 1) for k, v in pc.items()}
        result['power_curve_wkg'] = {f'{k}s': round(v / RIDER_WEIGHT_KG, 2) for k, v in pc.items()}
    else:
        result['power_curve'] = None

    # Zone distributions (counts of seconds at 1Hz)
    if (arrays['power_w'] > 0).any():
        result['power_zones_s'] = power_zones_distribution(
            arrays['power_w'], arrays['cadence_rpm'])
    if (arrays['hr_bpm'] > 0).any():
        result['hr_zones_s'] = hr_zones_distribution(arrays['hr_bpm'])

    # Climbs (only meaningful if there's GPS+altitude variation)
    if arrays['altitude_m'].max() - arrays['altitude_m'].min() > 30:
        climbs = find_climbs(arrays['distance_m'], arrays['altitude_m'])
        result['climbs'] = climbs

    # Lap structure (interval detection)
    interval_laps = [l for l in laps if l.get('avg_power', 0) > FTP * 0.95]
    result['intervals_detected'] = len(interval_laps)

    return result


def format_markdown(result):
    """Format an analysis dict as markdown for rides/analyses/."""
    lines = []
    lines.append(f"# Ride analysis — {Path(result['file']).stem}\n")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"Source: `{result['file']}`\n")
    lines.append("\n## Summary\n")
    lines.append(f"- **Date**: {result['start_time']}")
    lines.append(f"- **Sport**: {result['sport']}/{result['sub_sport']}")
    lines.append(f"- **Distance**: {result['distance_km']:.2f} km")
    lines.append(f"- **Timer time**: {result['timer_time_s']/60:.1f} min")
    if result['auto_pause_s'] > 30:
        lines.append(f"- **Auto-pause**: {result['auto_pause_s']/60:.1f} min "
                     f"(elapsed: {result['elapsed_time_s']/60:.1f} min)")
    lines.append(f"- **Ascent**: {result['total_ascent_m']:.0f} m")
    lines.append("")
    lines.append("## Stored TrainingPeaks values (use these, do not recompute)\n")
    lines.append(f"- **TSS**: {result['tss']:.1f}")
    lines.append(f"- **NP**: {result['normalized_power_w']:.0f} W")
    lines.append(f"- **IF**: {result['intensity_factor']:.3f}")
    lines.append(f"- **FTP used in calc**: {result['threshold_power_used_w']:.0f} W")
    lines.append(f"- **Avg power**: {result['avg_power_w']:.0f} W | "
                 f"**Max**: {result['max_power_w']:.0f} W")
    lines.append(f"- **Avg HR**: {result['avg_hr_bpm']:.0f} bpm | "
                 f"**Max**: {result['max_hr_bpm']:.0f} bpm")
    lines.append(f"- **Avg cadence**: {result['avg_cadence_rpm']:.0f} rpm")

    if result.get('power_curve'):
        lines.append("\n## Power curve\n")
        lines.append("| Duration | Power | W/kg |")
        lines.append("|---|---|---|")
        for d, p in result['power_curve'].items():
            wkg = result['power_curve_wkg'].get(d, 0)
            lines.append(f"| {d} | {p:.0f} W | {wkg:.2f} |")

    if result.get('climbs'):
        lines.append(f"\n## Climbs ({len(result['climbs'])})\n")
        lines.append("| # | Start km | Length | Gain | Avg % | Max % |")
        lines.append("|---|---|---|---|---|---|")
        for i, c in enumerate(result['climbs'], 1):
            lines.append(f"| {i} | {c['start_km']:.2f} | "
                         f"{c['length_m']:.0f} m | {c['gain_m']:.0f} m | "
                         f"{c['avg_grad_pct']:.1f}% | {c['max_grad_pct']:.1f}% |")

    if result.get('power_zones_s'):
        total = sum(result['power_zones_s'].values())
        if total > 0:
            lines.append(f"\n## Power zones (pedalling time, {total/60:.0f} min total)\n")
            for zone, secs in result['power_zones_s'].items():
                pct = secs / total * 100
                lines.append(f"- **{zone}**: {secs/60:.1f} min ({pct:.1f}%)")

    if result.get('hr_zones_s'):
        total = sum(result['hr_zones_s'].values())
        if total > 0:
            lines.append(f"\n## HR zones ({total/60:.0f} min with HR)\n")
            for zone, secs in result['hr_zones_s'].items():
                pct = secs / total * 100
                lines.append(f"- **{zone}**: {secs/60:.1f} min ({pct:.1f}%)")

    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Analyse FIT files for cycling.')
    parser.add_argument('files', nargs='+', help='FIT file path(s)')
    parser.add_argument('--json', action='store_true',
                        help='Output JSON instead of markdown')
    parser.add_argument('--save', action='store_true',
                        help='Save markdown output to rides/analyses/<name>.md')
    args = parser.parse_args()

    for f in args.files:
        result = analyse(f)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            md = format_markdown(result)
            print(md)
            if args.save:
                stem = Path(f).stem
                out_dir = Path(__file__).parent.parent / 'rides' / 'analyses'
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f'{stem}.md'
                out_path.write_text(md)
                print(f'\n[Saved to {out_path}]', file=sys.stderr)


if __name__ == '__main__':
    main()
