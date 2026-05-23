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
from climb_categories import CATEGORIES, categorise
from chart_climb_detail import grade_colour, climb_stats, resample_segment, plot_climb_detail

# Reference benchmark: a 1.45 km climb at 9% average grade ≈ index 13.05
# (high Cat 3). Used to give the rider a "% of a known Cat 3" reading.
REFERENCE_CLIMB_INDEX = 1.45 * 9.0  # 13.05

# GRADE_COLOURS is defined in chart_climb_detail; re-export for plot_overview's legend.
from chart_climb_detail import GRADE_COLOURS


def plot_overview(arrays, climbs, out_path, ride_name='', bike_name=''):
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
    if bike_name:
        title = f"{bike_name} — {ride_name}" if ride_name else bike_name
    else:
        title = 'Ride profile' + (f' — {ride_name}' if ride_name else '')
    ax.set_title(title, fontsize=13, fontweight='bold', loc='left')
    ax.grid(True, alpha=0.3)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return True


def write_markdown(climbs, arrays, fit_path, chart_paths, out_path, bike=None, surface=''):
    """Produce the climb-categorisation markdown."""
    lines = []
    stem = Path(fit_path).stem
    lines.append(f"# Climbs — {stem}\n")
    lines.append(f"Source: `{fit_path}`\n")
    if bike is not None:
        lines.append(f"**Bike:** {bike.name} (`{bike.slug}`)  \n")
        lines.append(f"**Surface:** {surface}  \n\n")

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
    from bike_cli import add_bike_args, resolve_bike
    parser = argparse.ArgumentParser(
        description='Categorise climbs and generate TdF-style profile charts.')
    parser.add_argument('files', nargs='+', help='FIT file path(s)')
    parser.add_argument('--out-dir', default=None,
                        help='Base dir (default: rides/ relative to repo root)')
    add_bike_args(parser)
    args = parser.parse_args()

    bike, surface, assist_level = resolve_bike(args)  # noqa: F841 — assist_level unused here

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
        plot_overview(arrays, cat_climbs, overview_path, ride_name=stem, bike_name=bike.name)
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
        write_markdown(cat_climbs, arrays, str(path), chart_paths, md_path,
                       bike=bike, surface=surface)
        print(f'  Wrote {md_path}')

        total_pts = sum(categorise(c['length_m']/1000, c['avg_grad_pct'])[1]
                        for c in cat_climbs)
        print(f'  Total: {len(cat_climbs)} climbs, {total_pts} KOM pts')


if __name__ == '__main__':
    main()
