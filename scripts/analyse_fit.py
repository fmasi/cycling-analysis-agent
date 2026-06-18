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
# Climb-detection primitives live in climb_detect (shared with analyse_gpx).
# Re-exported here so existing `from analyse_fit import find_climbs` keeps working.
from climb_detect import median_filter_1d, compute_max_grade, find_climbs  # noqa: F401


# Moving-time threshold: speed below this counts as "stopped". 1 km/h chosen
# to catch genuine stops without flagging slow Brompton climbing or queueing
# traffic. Window of 5 s smooths out single-sample GPS speed glitches.
MOVING_SPEED_KMH = 1.0
MOVING_WIN_S = 5


# Ascent-from-records smoothing window (seconds, at 1 Hz).
#
# Used only when session.total_ascent is missing (Apple Watch case) — Wahoo
# FITs prefer the head unit's own session value. Calibrated 12 May 2026 against
# a single dual-device ride (Apple Watch + Wahoo ROAM on the same Cély test
# loop):
#
#   win=15s (old default)  → AW 193m, WR 187m  (vs Wahoo session 158, DEM 146)
#   win=45s (current)      → AW 159m, WR 156m  ← matches Wahoo session ±2m
#   win=61s                → AW 149m, WR 146m  ← matches DEM but undershoots Wahoo
#
# 45 s chosen so the from-records fallback agrees with the Wahoo head unit's
# own algorithm, which is what the rider sees in TrainingPeaks. Minimum-delta
# thresholds (e.g. ignore positive diffs < 0.2 m) were tested and rejected —
# they kill legitimate climbing signal at any window setting (a 5% climb at
# 5 km/h = ~0.07 m rise per 1 Hz sample, below typical thresholds).
#
# Single-ride calibration — re-tune once 3-5 more dual-device rides are on disk.
ASCENT_MEDIAN_WINDOW_S = 45


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


def _first_present(r, *names):
    """First non-None value among names. NaN if all missing.

    Replaces the `r.get('enhanced_x', r.get('x', 0)) or 0` pattern, which
    silently zero-fills when a key is present with value None (which fitparse
    does when the underlying FIT field has no value). Zero-fill corrupts
    continuous physical quantities (altitude, distance) downstream.
    """
    for n in names:
        v = r.get(n)
        if v is not None:
            return v
    return np.nan


def _interp_nan(arr):
    """Linear-interpolate over NaN samples in-place. Used for altitude /
    distance so a single missing record doesn't collapse the array to 0."""
    n = len(arr)
    if n == 0:
        return arr
    mask = np.isnan(arr)
    if not mask.any():
        return arr
    if mask.all():
        return np.zeros_like(arr)
    idx = np.arange(n)
    arr[mask] = np.interp(idx[mask], idx[~mask], arr[~mask])
    return arr


def to_arrays(records):
    """Convert record list to aligned numpy arrays.

    Continuous physical quantities (altitude, distance) are interpolated over
    missing samples. Sensor-driven channels (power, HR, cadence, speed) keep
    0 as the "no sample" sentinel — downstream code filters with `> 0`.
    """
    if not records:
        return None
    t0 = records[0]['timestamp']

    time_s = np.array([(r['timestamp'] - t0).total_seconds() for r in records])
    distance_m = np.array([_first_present(r, 'distance') for r in records], dtype=float)
    altitude_m = np.array([_first_present(r, 'enhanced_altitude', 'altitude')
                           for r in records], dtype=float)
    speed_ms = np.array([_first_present(r, 'enhanced_speed', 'speed')
                         for r in records], dtype=float)
    power_w = np.array([_first_present(r, 'power') for r in records], dtype=float)
    hr_bpm = np.array([_first_present(r, 'heart_rate') for r in records], dtype=float)
    cadence_rpm = np.array([_first_present(r, 'cadence') for r in records], dtype=float)

    _interp_nan(altitude_m)
    _interp_nan(distance_m)

    return {
        'time_s': time_s,
        'distance_m': distance_m,
        'altitude_m': altitude_m,
        'speed_kmh': np.nan_to_num(speed_ms, nan=0.0) * 3.6,
        'power_w': np.nan_to_num(power_w, nan=0.0),
        'hr_bpm': np.nan_to_num(hr_bpm, nan=0.0),
        'cadence_rpm': np.nan_to_num(cadence_rpm, nan=0.0),
    }


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
    """Time in each Karvonen HR zone (seconds), including a sub-Z1 bucket.

    The sub-Z1 bucket (HR > 0 but below Z1 floor) captures very-easy /
    coasting time. Omitting it made easy-spin rides look like "no HR data"
    when the rider was simply below the Z1 threshold most of the ride.
    """
    valid = hr_arr[hr_arr > 0]
    if len(valid) == 0:
        return {}
    # Karvonen: HR_target = (max - rest) * pct + rest
    bounds = [
        ('<Z1 Coast',    0.00, 0.50),
        ('Z1 Recovery',  0.50, 0.60),
        ('Z2 Aerobic',   0.60, 0.70),
        ('Z3 Tempo',     0.70, 0.80),
        ('Z4 Threshold', 0.80, 0.90),
        ('Z5 VO2max',    0.90, 1.00),
    ]
    hrr = max_hr - rest_hr
    out = {}
    for name, lo, hi in bounds:
        bpm_lo = rest_hr + hrr * lo
        bpm_hi = rest_hr + hrr * hi
        if name == 'Z5 VO2max':
            count = ((valid >= bpm_lo) & (valid <= max_hr + 10)).sum()
        elif name == '<Z1 Coast':
            count = ((valid > 0) & (valid < bpm_hi)).sum()
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

    # Sensor presence flags — used by format_markdown to suppress "0"-valued
    # rows for sensors the FIT didn't carry (e.g. Apple Watch: no power, no
    # cadence, no speed field).
    result['has_power'] = bool((arrays['power_w'] > 0).any())
    result['has_cadence'] = bool((arrays['cadence_rpm'] > 0).any())
    result['has_hr'] = bool((arrays['hr_bpm'] > 0).any())
    result['hr_samples'] = int((arrays['hr_bpm'] > 0).sum())
    result['hr_coverage_pct'] = (result['hr_samples'] / len(arrays['time_s']) * 100
                                  if len(arrays['time_s']) else 0)

    # Derive moving time from distance gradient when the FIT didn't apply
    # auto-pause (Apple Watch case: total_timer_time == total_elapsed_time,
    # but Apple Fitness app + Wahoo both auto-pause internally).
    #
    # Trigger: session marked no auto-pause AND distance signal has stopped
    # intervals (>60s worth of v < 1 km/h). Stored as `derived_moving_time_s`;
    # any hrTSS calculation should use this instead of `total_timer_time` to
    # avoid overstating TSS by the stopped-time fraction (16% on the
    # 12 May 2026 Cely test ride).
    derived_moving_s = 0
    timer = result['timer_time_s']
    elapsed = result['elapsed_time_s']
    no_autopause = elapsed > 0 and abs(elapsed - timer) < 1
    if len(arrays['time_s']) > MOVING_WIN_S:
        t = arrays['time_s']
        d = arrays['distance_m']
        v_kmh = np.zeros_like(t)
        for i in range(MOVING_WIN_S, len(t)):
            dt = t[i] - t[i - MOVING_WIN_S]
            if dt > 0:
                v_kmh[i] = (d[i] - d[i - MOVING_WIN_S]) / dt * 3.6
        derived_moving_s = int((v_kmh > MOVING_SPEED_KMH).sum())
    if no_autopause and derived_moving_s > 0 and (timer - derived_moving_s) > 60:
        result['derived_moving_time_s'] = float(derived_moving_s)
        result['no_autopause_applied'] = True
    else:
        result['no_autopause_applied'] = False

    # Derive ascent from altitude records if session didn't store it
    # (Apple Watch FITs don't carry session.total_ascent). Median filter
    # over per-sample baro, then sum positive deltas. See
    # ASCENT_MEDIAN_WINDOW_S for the calibration story.
    if result['total_ascent_m'] == 0 and len(arrays['altitude_m']) > 30:
        alt = arrays['altitude_m'].astype(float)
        alt_sm = median_filter_1d(alt, size=ASCENT_MEDIAN_WINDOW_S)
        d = np.diff(alt_sm)
        result['total_ascent_m'] = float(d[d > 0].sum())
        result['total_descent_m'] = float(-d[d < 0].sum())
        result['ascent_source'] = f'records ({ASCENT_MEDIAN_WINDOW_S}s median)'
    else:
        result['ascent_source'] = 'session'

    # Power curve
    if result['has_power']:
        pc = power_curve(arrays['power_w'])
        result['power_curve'] = {f'{k}s': round(v, 1) for k, v in pc.items()}
        result['power_curve_wkg'] = {f'{k}s': round(v / RIDER_WEIGHT_KG, 2) for k, v in pc.items()}
    else:
        result['power_curve'] = None

    # Zone distributions (counts of seconds at 1Hz)
    if result['has_power']:
        result['power_zones_s'] = power_zones_distribution(
            arrays['power_w'], arrays['cadence_rpm'])
    if result['has_hr']:
        result['hr_zones_s'] = hr_zones_distribution(arrays['hr_bpm'])

    # Climbs (only meaningful if there's GPS+altitude variation)
    if arrays['altitude_m'].max() - arrays['altitude_m'].min() > 30:
        climbs = find_climbs(arrays['distance_m'], arrays['altitude_m'])
        result['climbs'] = climbs

    # Lap structure (interval detection)
    interval_laps = [l for l in laps if l.get('avg_power', 0) > FTP * 0.95]
    result['intervals_detected'] = len(interval_laps)

    return result


def pair_analyse(path_a, path_b):
    """Analyse two FITs from the same ride and merge field-by-field.

    Pairing validation: same sport, start times within 30 min, distance
    within 5%.

    Merge rules:
        - "Terrain source" (distance / timer / ascent / temperature / climbs):
          the FIT whose `session.total_ascent` is populated (head-unit-stored
          ascent — typically the Wahoo or Garmin), with `auto-pause > 0` as
          a fallback signal. If neither file qualifies, falls back to file A.
        - HR: whichever file has HR samples > 0.
        - Power: whichever file has power samples > 0.

    Returns (merged_result, sources, warnings).
    """
    a = analyse(path_a)
    b = analyse(path_b)

    warnings = []
    sa, sb = a.get('sport', ''), b.get('sport', '')
    if sa and sb and sa != sb:
        warnings.append(f"Sport mismatch: '{sa}' vs '{sb}'")

    try:
        ta = datetime.fromisoformat(a['start_time']) if a['start_time'] else None
        tb = datetime.fromisoformat(b['start_time']) if b['start_time'] else None
        if ta and tb and abs((ta - tb).total_seconds()) > 1800:
            warnings.append(
                f"Start times {abs((ta-tb).total_seconds())/60:.0f} min apart — "
                "may not be the same ride"
            )
    except (ValueError, TypeError):
        pass

    if a['distance_km'] > 0 and b['distance_km'] > 0:
        dist_pct = (abs(a['distance_km'] - b['distance_km'])
                    / max(a['distance_km'], b['distance_km']) * 100)
        if dist_pct > 5:
            warnings.append(f"Distance differs by {dist_pct:.1f}% — verify same ride")

    # Pick terrain source: prefer session-stored ascent, then auto-pause.
    def _terrain_priority(r):
        score = 0
        if r['total_ascent_m'] > 0 and r.get('ascent_source') == 'session':
            score += 2
        if r['auto_pause_s'] > 30:
            score += 1
        return score

    if _terrain_priority(b) > _terrain_priority(a):
        terrain, other = b, a
        terrain_label, other_label = 'B', 'A'
        terrain_path, other_path = path_b, path_a
    else:
        terrain, other = a, b
        terrain_label, other_label = 'A', 'B'
        terrain_path, other_path = path_a, path_b

    # HR source
    hr_data, hr_source = None, None
    if terrain.get('has_hr'):
        hr_data, hr_source = terrain, terrain_label
    elif other.get('has_hr'):
        hr_data, hr_source = other, other_label

    # Power source
    pwr_data, power_source = None, None
    if terrain.get('has_power'):
        pwr_data, power_source = terrain, terrain_label
    elif other.get('has_power'):
        pwr_data, power_source = other, other_label

    merged = dict(terrain)
    merged['file'] = str(terrain_path)
    merged['paired_with'] = str(other_path)

    if hr_data:
        for k in ('avg_hr_bpm', 'max_hr_bpm', 'has_hr', 'hr_samples',
                  'hr_coverage_pct', 'hr_zones_s'):
            if k in hr_data:
                merged[k] = hr_data[k]

    if pwr_data:
        for k in ('tss', 'normalized_power_w', 'intensity_factor', 'avg_power_w',
                  'max_power_w', 'threshold_power_used_w', 'avg_cadence_rpm',
                  'power_curve', 'power_curve_wkg', 'power_zones_s',
                  'has_power', 'has_cadence'):
            if k in pwr_data:
                merged[k] = pwr_data[k]

    sources = {
        'terrain_label': terrain_label,
        'terrain_path': str(terrain_path),
        'other_label': other_label,
        'other_path': str(other_path),
        'hr_source': hr_source,
        'power_source': power_source,
        'a': {'path': str(path_a), 'start_time': a['start_time'],
              'distance_km': a['distance_km'], 'timer_s': a['timer_time_s'],
              'elapsed_s': a['elapsed_time_s'], 'ascent_m': a['total_ascent_m'],
              'ascent_source': a.get('ascent_source'),
              'has_hr': a.get('has_hr', False), 'has_power': a.get('has_power', False)},
        'b': {'path': str(path_b), 'start_time': b['start_time'],
              'distance_km': b['distance_km'], 'timer_s': b['timer_time_s'],
              'elapsed_s': b['elapsed_time_s'], 'ascent_m': b['total_ascent_m'],
              'ascent_source': b.get('ascent_source'),
              'has_hr': b.get('has_hr', False), 'has_power': b.get('has_power', False)},
    }
    return merged, sources, warnings


def format_markdown_paired(merged, sources, warnings):
    """Format a paired-FIT analysis with explicit source attribution."""
    lines = []
    title = Path(merged['file']).stem
    lines.append(f"# Ride analysis — {title} (paired)\n")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    lines.append("## Sources\n")
    lines.append(f"- **{sources['terrain_label']}** "
                 f"(terrain / distance / timer / climbs): "
                 f"`{sources['terrain_path']}`")
    lines.append(f"- **{sources['other_label']}** (additional): "
                 f"`{sources['other_path']}`")
    if sources['hr_source']:
        lines.append(f"- HR from: **{sources['hr_source']}**")
    else:
        lines.append("- HR: _not recorded on either device_")
    if sources['power_source']:
        lines.append(f"- Power from: **{sources['power_source']}**")
    else:
        lines.append("- Power: _not recorded on either device_")

    if warnings:
        lines.append("\n**Pairing warnings**:")
        for w in warnings:
            lines.append(f"- ⚠️ {w}")

    lines.append("\n## Cross-device agreement\n")
    lines.append("| Metric | A | B | Δ |")
    lines.append("|---|---|---|---|")
    a, b = sources['a'], sources['b']
    if a['distance_km'] and b['distance_km']:
        d_abs = abs(a['distance_km'] - b['distance_km'])
        d_pct = d_abs / max(a['distance_km'], b['distance_km']) * 100
        lines.append(f"| Distance (km) | {a['distance_km']:.2f} | "
                     f"{b['distance_km']:.2f} | {d_abs:.2f} ({d_pct:.1f}%) |")
    if a['timer_s'] and b['timer_s']:
        lines.append(f"| Timer (min) | {a['timer_s']/60:.1f} | "
                     f"{b['timer_s']/60:.1f} | "
                     f"{abs(a['timer_s']-b['timer_s'])/60:.1f} |")
    if a['elapsed_s'] and b['elapsed_s']:
        lines.append(f"| Elapsed (min) | {a['elapsed_s']/60:.1f} | "
                     f"{b['elapsed_s']/60:.1f} | "
                     f"{abs(a['elapsed_s']-b['elapsed_s'])/60:.1f} |")
    if a['ascent_m'] or b['ascent_m']:
        a_src = f" _({a.get('ascent_source','session')})_" if a['ascent_m'] else ""
        b_src = f" _({b.get('ascent_source','session')})_" if b['ascent_m'] else ""
        lines.append(f"| Ascent (m) | {a['ascent_m']:.0f}{a_src} | "
                     f"{b['ascent_m']:.0f}{b_src} | "
                     f"{abs(a['ascent_m']-b['ascent_m']):.0f} |")

    body = format_markdown(merged).split('\n')
    for i, ln in enumerate(body):
        if ln.startswith('## Summary'):
            body = body[i:]
            break
    lines.append('\n' + '\n'.join(body))
    return '\n'.join(lines)


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
    if result.get('no_autopause_applied'):
        moving_min = result['derived_moving_time_s'] / 60
        timer_min = result['timer_time_s'] / 60
        gap_pct = (timer_min - moving_min) / timer_min * 100
        lines.append(f"- **Moving time** (derived, v > {MOVING_SPEED_KMH:.0f} km/h): "
                     f"{moving_min:.1f} min "
                     f"— device didn't auto-pause; timer overstates duration by "
                     f"{gap_pct:.0f}%. **Use moving time for hrTSS, not timer.**")
    ascent_src = result.get('ascent_source', 'session')
    ascent_label = "" if ascent_src == 'session' else " _(computed from records — session missing)_"
    lines.append(f"- **Ascent**: {result['total_ascent_m']:.0f} m{ascent_label}")
    lines.append("")

    has_power = result.get('has_power', result['avg_power_w'] > 0)
    has_cadence = result.get('has_cadence', result['avg_cadence_rpm'] > 0)

    if has_power:
        lines.append("## Stored TrainingPeaks values (use these, do not recompute)\n")
        lines.append(f"- **TSS**: {result['tss']:.1f}")
        lines.append(f"- **NP**: {result['normalized_power_w']:.0f} W")
        lines.append(f"- **IF**: {result['intensity_factor']:.3f}")
        lines.append(f"- **FTP used in calc**: {result['threshold_power_used_w']:.0f} W")
        lines.append(f"- **Avg power**: {result['avg_power_w']:.0f} W | "
                     f"**Max**: {result['max_power_w']:.0f} W")
    else:
        lines.append("## Stored TrainingPeaks values\n")
        lines.append("- **Power**: _not recorded_ (no power meter on this FIT — "
                     "use hrTSS via HR / duration)")

    if result['avg_hr_bpm'] > 0 or result['max_hr_bpm'] > 0:
        lines.append(f"- **Avg HR**: {result['avg_hr_bpm']:.0f} bpm | "
                     f"**Max**: {result['max_hr_bpm']:.0f} bpm")
    if has_cadence:
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
            cov = result.get('hr_coverage_pct', 0)
            samples = result.get('hr_samples', total)
            lines.append(f"\n## HR zones ({samples} samples, {cov:.0f}% record coverage)\n")
            for zone, secs in result['hr_zones_s'].items():
                pct = secs / total * 100
                lines.append(f"- **{zone}**: {secs/60:.1f} min ({pct:.1f}%)")

    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Analyse FIT files for cycling.')
    parser.add_argument('files', nargs='*', help='FIT file path(s) for single-file analysis')
    parser.add_argument('--pair', nargs=2, metavar=('FIT_A', 'FIT_B'),
                        help='Merge two FITs from the same ride into one '
                             'analysis (auto-picks best source per metric)')
    parser.add_argument('--json', action='store_true',
                        help='Output JSON instead of markdown')
    parser.add_argument('--save', action='store_true',
                        help='Save markdown output to rides/analyses/<name>.md')
    args = parser.parse_args()

    if not args.files and not args.pair:
        parser.error("either positional FIT files or --pair FIT_A FIT_B is required")

    if args.pair:
        merged, sources, warnings = pair_analyse(args.pair[0], args.pair[1])
        if args.json:
            print(json.dumps({'merged': merged, 'sources': sources,
                              'warnings': warnings}, indent=2, default=str))
        else:
            md = format_markdown_paired(merged, sources, warnings)
            print(md)
            if args.save:
                stem = Path(args.pair[0]).stem
                # Drop a leading date prefix if present, then tag -paired.
                out_dir = Path(__file__).parent.parent / 'rides' / 'analyses'
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f'{stem}-paired.md'
                out_path.write_text(md)
                print(f'\n[Saved to {out_path}]', file=sys.stderr)
        return

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
