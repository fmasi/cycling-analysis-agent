"""Per-climb TdF-style detail chart. Shared renderer for analyse_climbs (FIT)
and analyse_gpx (GPX). Moved verbatim from analyse_climbs.py."""
import matplotlib
matplotlib.use("Agg")  # headless; safe for tests and CLI
import matplotlib.pyplot as plt
import numpy as np

from climb_categories import categorise

GRADE_COLOURS = [
    (4,  '#e8e8e8'),   # 0-4%   light grey
    (6,  '#ffe680'),   # 4-6%   yellow
    (8,  '#ff9933'),   # 6-8%   orange
    (10, '#ff3333'),   # 8-10%  red
    (99, '#660000'),   # >10%   dark red
]


def grade_colour(grade_pct):
    """Colour for a grade segment (TdF convention)."""
    g = abs(grade_pct)
    for thresh, colour in GRADE_COLOURS:
        if g < thresh:
            return colour
    return GRADE_COLOURS[-1][1]


def climb_stats(arrays, start_km, end_km):
    """Compute power/HR/cadence/speed stats for a climb segment.

    Optional keys (power_w, hr_bpm, cadence_rpm, speed_kmh, time_s) are
    handled gracefully so this function works for GPX-only arrays that only
    carry distance_m and altitude_m.
    """
    d = arrays['distance_m']
    mask = (d >= start_km * 1000) & (d <= end_km * 1000)
    if not mask.any():
        return {}
    _empty = np.array([], dtype=float)

    def _masked(key):
        arr = arrays.get(key)
        if arr is None:
            return _empty
        return np.asarray(arr)[mask]

    powers = _masked('power_w')
    powers_pos = powers[powers > 0]
    hrs = _masked('hr_bpm')
    hrs_pos = hrs[hrs > 0]
    cads = _masked('cadence_rpm')
    cads_pos = cads[cads > 0]
    speeds = _masked('speed_kmh')
    speeds_pos = speeds[speeds > 0]
    times = _masked('time_s')
    duration_s = float(times[-1] - times[0]) if len(times) > 1 else 0
    return {
        'duration_s': duration_s,
        'avg_w':  float(powers_pos.mean()) if len(powers_pos) else 0,
        'np_w':   float((np.mean(powers_pos**4))**0.25) if len(powers_pos) else 0,
        'max_w':  float(powers.max()) if len(powers) else 0,
        'avg_hr': float(hrs_pos.mean()) if len(hrs_pos) else 0,
        'max_hr': float(hrs.max()) if len(hrs) else 0,
        'avg_cad':float(cads_pos.mean()) if len(cads_pos) else 0,
        'avg_kmh':float(speeds_pos.mean()) if len(speeds_pos) else 0,
    }


def resample_segment(arrays, start_km, end_km, step_m=50):
    """Return resampled (d_grid_m, alt_m) over the climb at fixed spacing."""
    d = arrays['distance_m']
    a = arrays['altitude_m']
    mask = (d >= start_km * 1000) & (d <= end_km * 1000)
    if not mask.any():
        return np.array([]), np.array([])
    d_seg = d[mask]
    a_seg = a[mask]
    # Edge-aware smoothing: pad with edge values so the boundary samples
    # aren't pulled toward zero by convolve(mode='same').
    if len(a_seg) >= 5:
        win = min(11, len(a_seg) // 3)
        if win % 2 == 0:
            win += 1
        pad = win // 2
        a_padded = np.pad(a_seg, pad, mode='edge')
        a_seg = np.convolve(a_padded, np.ones(win)/win, mode='valid')
    grid = np.arange(d_seg[0], d_seg[-1], step_m)
    alt = np.interp(grid, d_seg, a_seg)
    return grid, alt


def plot_climb_detail(arrays, climb, idx, out_path):
    """TdF-style elevation profile for a single climb."""
    grid, alt = resample_segment(arrays, climb['start_km'], climb['end_km'], step_m=50)
    if len(grid) < 4:
        return False

    name, pts, badge_col, fill_col, index = categorise(
        climb['length_m']/1000, climb['avg_grad_pct'])
    stats = climb_stats(arrays, climb['start_km'], climb['end_km'])

    # Per-100m grade segments
    rel_d = (grid - grid[0]) / 1000  # km from climb start

    fig, ax = plt.subplots(figsize=(11, 5.5))
    fig.patch.set_facecolor('white')

    # Shade per 100m by grade
    base = alt.min() - max(5, (alt.max() - alt.min()) * 0.05)
    for i in range(0, len(grid) - 1, 2):  # 100m chunks (2x 50m)
        i_end = min(i + 2, len(grid) - 1)
        if i_end <= i:
            break
        d_chunk = grid[i_end] - grid[i]
        a_chunk = alt[i_end] - alt[i]
        if d_chunk <= 0:
            continue
        chunk_grade = a_chunk / d_chunk * 100
        col = grade_colour(chunk_grade)
        x = [rel_d[i], rel_d[i_end]]
        y_top = [alt[i], alt[i_end]]
        y_bot = [base, base]
        ax.fill_between(x, y_bot, y_top, color=col, edgecolor='none')

    # Profile line on top
    ax.plot(rel_d, alt, color='black', linewidth=1.6)

    # Per-100m grade labels (only on chunks ≥6%)
    for i in range(0, len(grid) - 1, 2):
        i_end = min(i + 2, len(grid) - 1)
        if i_end <= i:
            break
        d_chunk = grid[i_end] - grid[i]
        a_chunk = alt[i_end] - alt[i]
        if d_chunk <= 0:
            continue
        g = a_chunk / d_chunk * 100
        if g >= 6:
            x_mid = (rel_d[i] + rel_d[i_end]) / 2
            y_top = max(alt[i], alt[i_end])
            ax.annotate(f'{g:.1f}%', xy=(x_mid, y_top),
                        xytext=(0, 6), textcoords='offset points',
                        ha='center', fontsize=8,
                        color='black' if g < 10 else 'darkred',
                        fontweight='bold' if g >= 10 else 'normal')

    # Category badge top-right
    badge_text = (f"{name} · {climb['length_m']/1000:.2f} km · "
                  f"{climb['avg_grad_pct']:.1f}% avg · "
                  f"{climb['max_grad_pct']:.1f}% max")
    ax.text(0.99, 0.97, badge_text, transform=ax.transAxes,
            ha='right', va='top', fontsize=10, fontweight='bold',
            color='white',
            bbox=dict(boxstyle='round,pad=0.5', facecolor=badge_col,
                      edgecolor='none'))

    # Stats annotation bottom-right
    if stats:
        mins = int(stats['duration_s'] // 60)
        secs = int(stats['duration_s'] % 60)
        stat_text = (f"{mins}:{secs:02d} · {stats['avg_w']:.0f} W avg · "
                     f"{stats['avg_kmh']:.1f} km/h · "
                     f"{stats['avg_cad']:.0f} rpm · "
                     f"max HR {stats['max_hr']:.0f}")
        ax.text(0.99, 0.04, stat_text, transform=ax.transAxes,
                ha='right', va='bottom', fontsize=9,
                color='#444444',
                bbox=dict(boxstyle='round,pad=0.4', facecolor='white',
                          edgecolor='#cccccc'))

    # Points badge top-left
    ax.text(0.01, 0.97, f"{pts} pt{'s' if pts != 1 else ''}",
            transform=ax.transAxes, ha='left', va='top',
            fontsize=11, fontweight='bold',
            color=badge_col,
            bbox=dict(boxstyle='circle,pad=0.4', facecolor='white',
                      edgecolor=badge_col, linewidth=2))

    ax.set_xlim(rel_d[0], rel_d[-1])
    ax.set_ylim(base, alt.max() + (alt.max() - alt.min()) * 0.15 + 5)
    ax.set_xlabel('Distance from climb start (km)')
    ax.set_ylabel('Elevation (m)')
    ax.set_title(f"Climb {idx} — km {climb['start_km']:.2f}–{climb['end_km']:.2f}",
                 fontsize=13, fontweight='bold', loc='left')
    ax.grid(True, alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Legend for grade colours
    legend_y = 1.06
    legend_labels = ['<4%', '4-6%', '6-8%', '8-10%', '>10%']
    legend_colours = [c[1] for c in GRADE_COLOURS]
    for i, (lbl, col) in enumerate(zip(legend_labels, legend_colours)):
        ax.text(0.05 + i * 0.10, legend_y, lbl, transform=ax.transAxes,
                ha='center', fontsize=8,
                bbox=dict(boxstyle='round,pad=0.25', facecolor=col,
                          edgecolor='#888888'))

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return True
