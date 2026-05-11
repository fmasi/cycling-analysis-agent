"""
Climb categorisation + TdF-style profile charts.

Run after `analyse_fit.py` to add UCI-style climb categories and elevation
profile charts to long-ride analyses.

    python scripts/analyse_climbs.py rides/<name>.fit
    python scripts/analyse_climbs.py rides/<name>.fit --out-dir rides/

Outputs:
    rides/analyses/<stem>-climbs.md              (always, if any categorised climbs)
    rides/charts/<stem>-overview.png             (always, if any climbs)
    rides/charts/<stem>-climb<N>.png             (per Cat 3+ climb)

Categorisation: index = length_km × avg_grade_pct
    <2    uncategorised   0 pts
    2-6   Cat 4           1 pt
    6-16  Cat 3           2 pts
    16-40 Cat 2           5 pts
    40-80 Cat 1          10 pts
    >80   HC             20 pts

Reference benchmark: a 1.45 km climb at 9% average grade = index 13.05 → high Cat 3.
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from analyse_fit import parse_fit, to_arrays, find_climbs


CATEGORIES = [
    # (lower_index_inclusive, name, points, badge_colour, fill_colour)
    (80, 'HC',    20, '#000000', '#440000'),
    (40, 'Cat 1', 10, '#cc4400', '#ff7700'),
    (16, 'Cat 2',  5, '#cc9900', '#ffcc33'),
    ( 6, 'Cat 3',  2, '#0066cc', '#3399ff'),
    ( 2, 'Cat 4',  1, '#006633', '#33aa66'),
    ( 0, 'uncat',  0, '#888888', '#cccccc'),
]

GRADE_COLOURS = [
    (4,  '#e8e8e8'),   # 0-4%   light grey
    (6,  '#ffe680'),   # 4-6%   yellow
    (8,  '#ff9933'),   # 6-8%   orange
    (10, '#ff3333'),   # 8-10%  red
    (99, '#660000'),   # >10%   dark red
]

# Reference benchmark: a 1.45 km climb at 9% average grade ≈ index 13.05
# (high Cat 3). Used to give the rider a "% of a known Cat 3" reading.
REFERENCE_CLIMB_INDEX = 1.45 * 9.0  # 13.05


def categorise(length_km, avg_grade_pct):
    """Return (category_name, kom_points, badge_colour, fill_colour, index)."""
    index = length_km * avg_grade_pct
    for threshold, name, points, badge, fill in CATEGORIES:
        if index >= threshold:
            return name, points, badge, fill, index
    return 'uncat', 0, '#888888', '#cccccc', index


def grade_colour(grade_pct):
    """Colour for a grade segment (TdF convention)."""
    g = abs(grade_pct)
    for thresh, colour in GRADE_COLOURS:
        if g < thresh:
            return colour
    return GRADE_COLOURS[-1][1]


def climb_stats(arrays, start_km, end_km):
    """Compute power/HR/cadence/speed stats for a climb segment."""
    d = arrays['distance_m']
    mask = (d >= start_km * 1000) & (d <= end_km * 1000)
    if not mask.any():
        return {}
    powers = arrays['power_w'][mask]
    powers_pos = powers[powers > 0]
    hrs = arrays['hr_bpm'][mask]
    hrs_pos = hrs[hrs > 0]
    cads = arrays['cadence_rpm'][mask]
    cads_pos = cads[cads > 0]
    speeds = arrays['speed_kmh'][mask]
    speeds_pos = speeds[speeds > 0]
    times = arrays['time_s'][mask]
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


def plot_overview(arrays, climbs, out_path, ride_name=''):
    """Whole-ride elevation profile with climbs shaded by category."""
    d = arrays['distance_m'] / 1000
    a = arrays['altitude_m']
    if len(d) < 10 or a.max() - a.min() < 5:
        return False
    # Drop obviously-bad altitude samples (zero or negative — sensor dropouts)
    if (a > 0).any():
        a = np.where(a > 0, a, np.nan)
        # Forward-fill NaN with previous valid value
        nan_mask = np.isnan(a)
        if nan_mask.any():
            valid_idx = np.where(~nan_mask)[0]
            if len(valid_idx) > 0:
                a = np.interp(np.arange(len(a)), valid_idx, a[valid_idx])
    # Edge-aware smoothing
    win = min(31, len(a) // 5)
    if win >= 3:
        if win % 2 == 0:
            win += 1
        pad = win // 2
        a_padded = np.pad(a, pad, mode='edge')
        a_s = np.convolve(a_padded, np.ones(win)/win, mode='valid')
    else:
        a_s = a

    fig, ax = plt.subplots(figsize=(13, 5))
    fig.patch.set_facecolor('white')

    base = a_s.min() - 10
    ax.fill_between(d, base, a_s, color='#dddddd', edgecolor='none')
    ax.plot(d, a_s, color='#666666', linewidth=1.0)

    total_pts = 0
    for i, c in enumerate(climbs, 1):
        name, pts, badge_col, fill_col, index = categorise(
            c['length_m']/1000, c['avg_grad_pct'])
        total_pts += pts
        mask = (d >= c['start_km']) & (d <= c['end_km'])
        if mask.any():
            ax.fill_between(d[mask], base, a_s[mask],
                            color=fill_col, edgecolor='none', alpha=0.85)
            # Label at peak
            peak_idx = np.argmax(a_s[mask])
            peak_d = d[mask][peak_idx]
            peak_a = a_s[mask][peak_idx]
            label = f"#{i} {name}\n{pts}pt"
            ax.annotate(label, xy=(peak_d, peak_a),
                        xytext=(0, 12), textcoords='offset points',
                        ha='center', fontsize=8, fontweight='bold',
                        color=badge_col,
                        bbox=dict(boxstyle='round,pad=0.3',
                                  facecolor='white',
                                  edgecolor=badge_col, linewidth=1.2))

    ax.text(0.99, 0.97,
            f"Day total: {total_pts} KOM pts",
            transform=ax.transAxes, ha='right', va='top',
            fontsize=12, fontweight='bold', color='white',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#cc0000',
                      edgecolor='none'))

    ax.set_xlim(d.min(), d.max())
    ax.set_ylim(base, a_s.max() + 30)
    ax.set_xlabel('Distance (km)')
    ax.set_ylabel('Elevation (m)')
    title = 'Ride profile' + (f' — {ride_name}' if ride_name else '')
    ax.set_title(title, fontsize=13, fontweight='bold', loc='left')
    ax.grid(True, alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return True


def write_markdown(climbs, arrays, fit_path, chart_paths, out_path):
    """Produce the climb-categorisation markdown."""
    lines = []
    stem = Path(fit_path).stem
    lines.append(f"# Climbs — {stem}\n")
    lines.append(f"Source: `{fit_path}`\n")

    if not climbs:
        lines.append("No categorised climbs detected.\n")
        out_path.write_text('\n'.join(lines))
        return

    total_pts = 0
    rows = []
    for i, c in enumerate(climbs, 1):
        name, pts, _, _, index = categorise(
            c['length_m']/1000, c['avg_grad_pct'])
        total_pts += pts
        rows.append((i, c, name, pts, index))

    lines.append("## Categories & KOM points\n")
    lines.append("| # | Where | Length | Gain | Avg % | Max % | Index | Category | Points |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, c, name, pts, index in rows:
        lines.append(
            f"| {i} | km {c['start_km']:.2f} | {c['length_m']:.0f} m | "
            f"{c['gain_m']:.0f} m | {c['avg_grad_pct']:.1f}% | "
            f"{c['max_grad_pct']:.1f}% | {index:.2f} | **{name}** | {pts} |"
        )
    lines.append(f"\n**Day total: {total_pts} KOM points** "
                 f"(across {len(climbs)} climb{'s' if len(climbs) != 1 else ''})\n")

    # Per-climb stats
    lines.append("## Per-climb performance\n")
    lines.append("| # | Time | Avg W | NP W | Max HR | Avg cad | Avg km/h |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, c, name, pts, index in rows:
        s = climb_stats(arrays, c['start_km'], c['end_km'])
        if not s:
            continue
        mins = int(s['duration_s'] // 60)
        secs = int(s['duration_s'] % 60)
        lines.append(
            f"| {i} | {mins}:{secs:02d} | {s['avg_w']:.0f} | "
            f"{s['np_w']:.0f} | {s['max_hr']:.0f} | "
            f"{s['avg_cad']:.0f} | {s['avg_kmh']:.1f} |"
        )

    # Reference benchmark — a 1.45 km / 9% climb (high Cat 3)
    lines.append("\n## Reference: high Cat 3 benchmark\n")
    lines.append(f"Reference climb = 1.45 km × 9% = index **{REFERENCE_CLIMB_INDEX:.2f}** "
                 f"(high **Cat 3**).\n")
    if rows:
        hardest = max(rows, key=lambda r: r[4])
        ratio = hardest[4] / REFERENCE_CLIMB_INDEX * 100
        lines.append(
            f"Today's hardest climb (#{hardest[0]}) had index "
            f"**{hardest[4]:.2f}** — **{ratio:.0f}%** of the reference Cat 3.\n"
        )

    if chart_paths:
        lines.append("\n## Charts\n")
        for p in chart_paths:
            rel = Path(p).relative_to(Path(out_path).parent.parent)
            lines.append(f"- `{rel}`")

    out_path.write_text('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Categorise climbs and generate TdF-style profile charts.')
    parser.add_argument('files', nargs='+', help='FIT file path(s)')
    parser.add_argument('--out-dir', default=None,
                        help='Base dir (default: rides/ relative to repo root)')
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    base = Path(args.out_dir) if args.out_dir else repo_root / 'rides'
    analyses_dir = base / 'analyses'
    charts_dir = base / 'charts'
    analyses_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    for f in args.files:
        path = Path(f)
        stem = path.stem
        print(f'\n=== {stem} ===')

        session, records, _ = parse_fit(path)
        arrays = to_arrays(records)
        if arrays is None:
            print('  No records — skipping.')
            continue

        if arrays['altitude_m'].max() - arrays['altitude_m'].min() < 30:
            print('  Insufficient elevation variation — skipping.')
            continue

        climbs = find_climbs(arrays['distance_m'], arrays['altitude_m'])
        # Filter: only categorised (index >= 2)
        cat_climbs = [c for c in climbs
                      if (c['length_m']/1000) * c['avg_grad_pct'] >= 2.0]

        if not cat_climbs:
            print('  No categorised climbs (index >= 2) — skipping.')
            continue

        # Overview
        overview_path = charts_dir / f'{stem}-overview.png'
        plot_overview(arrays, cat_climbs, overview_path, ride_name=stem)
        print(f'  Wrote {overview_path}')
        chart_paths = [overview_path]

        # Per-climb detail for Cat 3+
        for i, c in enumerate(cat_climbs, 1):
            name, pts, *_ = categorise(c['length_m']/1000, c['avg_grad_pct'])
            if pts >= 2:  # Cat 3 or harder
                detail_path = charts_dir / f'{stem}-climb{i}.png'
                if plot_climb_detail(arrays, c, i, detail_path):
                    print(f'  Wrote {detail_path}')
                    chart_paths.append(detail_path)

        # Markdown
        md_path = analyses_dir / f'{stem}-climbs.md'
        write_markdown(cat_climbs, arrays, str(path), chart_paths, md_path)
        print(f'  Wrote {md_path}')

        total_pts = sum(categorise(c['length_m']/1000, c['avg_grad_pct'])[1]
                        for c in cat_climbs)
        print(f'  Total: {len(cat_climbs)} climbs, {total_pts} KOM pts')


if __name__ == '__main__':
    main()
