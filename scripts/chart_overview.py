"""
Reusable TdF-style overview chart for cycling routes/rides.

3-row GridSpec layout:
    [waypoint lane  : 15%]  icons + labels with leader lines down to profile
    [profile        : 70%]  grade-coloured area + climb info boxes
    [grade strip    : 15%]  Strava-style segment-coloured band

Palette (Strava-aligned grade ramp + ColorBrewer Set2 waypoints + TdF cat colours).
Uses adjustText for automatic label-collision avoidance on the waypoint lane.
"""

from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from adjustText import adjust_text


# ============== WAYPOINT PARSING (GPX) ==============

def parse_waypoints(gpx_path):
    """Extract <wpt> entries from a GPX file."""
    tree = ET.parse(str(gpx_path))
    root = tree.getroot()
    ns = {'gpx': root.tag.split('}')[0].strip('{')}
    out = []
    for w in root.findall('gpx:wpt', ns):
        nm_el = w.find('gpx:name', ns)
        sym_el = w.find('gpx:sym', ns)
        desc_el = w.find('gpx:desc', ns)
        out.append({
            'lat': float(w.attrib['lat']),
            'lon': float(w.attrib['lon']),
            'name': nm_el.text if nm_el is not None else '',
            'sym': sym_el.text if sym_el is not None else '',
            'desc': desc_el.text if desc_el is not None else '',
        })
    return out


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * r * math.asin(math.sqrt(a))


def project_to_route(wpt, lats, lons, dists):
    """Find nearest track point. Returns (km_along, off_route_m)."""
    import numpy as np
    dlat = lats - wpt['lat']
    dlon = (lons - wpt['lon']) * np.cos(np.radians(wpt['lat']))
    i = int(np.argmin(dlat**2 + dlon**2))
    off = _haversine_m(lats[i], lons[i], wpt['lat'], wpt['lon'])
    return float(dists[i] / 1000), float(off)


def auto_classify_waypoint(wpt, km, total_km):
    """Heuristic mapping from raw GPX <wpt> to chart category + display label.
    Returns (cat, display_name) or None to drop."""
    name = (wpt.get('name') or '').strip()
    name_u = name.upper()
    sym = (wpt.get('sym') or '').lower()

    # Drop pure route-decision flags
    if 'TO BE CHECKED' in name_u or name_u.startswith('CHECK ') or '?' in name:
        return None

    # Start: first waypoint within ~0.5 km of start, OR explicit "start"/"home" name
    if km < 0.5 and ('TOWN' in name_u or 'START' in name_u or 'HOME' in name_u):
        return ('Start', name or 'Start')
    # Pub / restaurant
    if 'PUB' in name_u or sym == 'restaurant' or 'BAR' in name_u or 'CAFE' in name_u:
        return ('Pub', name or 'Pub')
    # Water
    if 'WATER' in name_u or 'WATHER' in name_u or 'shopping' in sym:
        return ('Water', 'Water')
    # Fuel
    if 'FUEL' in name_u or 'EAT' in name_u or 'FOOD' in name_u:
        return ('Fuel', 'Fuel')
    # Summit / castle / generic POI
    if sym == 'summit' or 'CASTLE' in name_u or 'PEAK' in name_u or 'VIEW' in name_u:
        return ('POI', name or 'POI')
    # Default: drop unrecognised symbols to avoid clutter
    return None


def enumerate_waypoints(gpx_path, lats, lons, dists,
                         dedupe_within_m=80,
                         fuel_pub_min_separation_km=5.0):
    """Return ALL waypoints from the GPX with classification and a default
    keep/drop decision + reason. Caller can override.

    Each item: {idx, name, sym, km, off_m, cat, disp, decision, reason}
    decision ∈ {'KEEP', 'DROP'}.
    """
    raw = parse_waypoints(gpx_path)
    items = []
    for i, w in enumerate(raw, 1):
        km, off = project_to_route(w, lats, lons, dists)
        cls = auto_classify_waypoint(w, km, dists[-1]/1000)
        if cls is None:
            cat, disp = ('?', w.get('name') or '?')
            decision, reason = 'DROP', 'unrecognised / route-flag'
        else:
            cat, disp = cls
            decision, reason = 'KEEP', ''
        items.append({
            'idx': i, 'name': w.get('name', ''), 'sym': w.get('sym', ''),
            'km': km, 'off_m': off, 'cat': cat, 'disp': disp,
            'decision': decision, 'reason': reason,
        })

    # Dedupe near-duplicates (same cat, very close).
    # If the new candidate has a SHORTER display name, swap — cleaner labels.
    last_kept_by_cat = {}
    for it in items:
        if it['decision'] != 'KEEP':
            continue
        prev = last_kept_by_cat.get(it['cat'])
        if prev and abs(it['km'] - prev['km']) * 1000 < dedupe_within_m:
            if len(it['disp']) < len(prev['disp']):
                # Swap: drop the previous, keep this one
                prev['decision'] = 'DROP'
                prev['reason'] = f'duplicate of #{it["idx"]} ({it["disp"]})'
                last_kept_by_cat[it['cat']] = it
            else:
                it['decision'] = 'DROP'
                it['reason'] = f'duplicate of #{prev["idx"]} ({prev["disp"]})'
        else:
            last_kept_by_cat[it['cat']] = it

    # Drop Fuel near a Pub (pub serves the food role)
    pub_items = [it for it in items if it['decision'] == 'KEEP' and it['cat'] == 'Pub']
    for it in items:
        if it['decision'] != 'KEEP' or it['cat'] != 'Fuel':
            continue
        for p in pub_items:
            if abs(it['km'] - p['km']) <= fuel_pub_min_separation_km:
                it['decision'] = 'DROP'
                it['reason'] = f'within {fuel_pub_min_separation_km:.0f} km of Pub #{p["idx"]}'
                break

    return items


def apply_overrides(items, include=(), exclude=()):
    """Apply --include / --exclude index lists to the enumerated waypoint set."""
    inc = set(include or [])
    exc = set(exclude or [])
    for it in items:
        if it['idx'] in inc:
            it['decision'] = 'KEEP'
            it['reason'] = (it['reason'] + ' [overridden: --include]').strip()
        if it['idx'] in exc:
            it['decision'] = 'DROP'
            it['reason'] = (it['reason'] + ' [overridden: --exclude]').strip()
    return items


def format_waypoint_table(items):
    """Pretty plain-text table for stderr display."""
    lines = []
    lines.append(f"{'#':>2}  {'km':>5}  {'decision':<6}  {'cat':<6}  "
                 f"{'name':<32}  {'reason / display':<40}")
    lines.append('-' * 100)
    for it in items:
        deci = '✓ keep' if it['decision'] == 'KEEP' else '✗ drop'
        notes = it['reason'] if it['decision'] == 'DROP' else f'shown as: "{it["disp"]}"'
        nm = (it['name'] or '?')[:32]
        lines.append(f"{it['idx']:>2}  {it['km']:>5.1f}  {deci:<6}  "
                     f"{it['cat']:<6}  {nm:<32}  {notes:<40}")
    kept = sum(1 for it in items if it['decision'] == 'KEEP')
    lines.append(f"\n{kept}/{len(items)} kept. "
                 f"Override with --include 1,4,7 / --exclude 2,5")
    return '\n'.join(lines)


def kept_for_render(items):
    """Reduce enumerate_waypoints output to the list render_overview wants."""
    return [{'km': it['km'], 'cat': it['cat'], 'disp': it['disp'], 'off_m': it['off_m']}
            for it in items if it['decision'] == 'KEEP']


# Backwards-compat shim — older callers used collect_waypoints_from_gpx
def collect_waypoints_from_gpx(gpx_path, lats, lons, dists, **kwargs):
    items = enumerate_waypoints(gpx_path, lats, lons, dists, **kwargs)
    return kept_for_render(items)


# ============== PALETTES ==============

GRADE_BANDS = [
    (3,   '#9ED99E'),  # < 3%   pale green
    (6,   '#F4C430'),  # 3-6%   amber
    (9,   '#E8743B'),  # 6-9%   warm orange
    (12,  '#C8302C'),  # 9-12%  deep red
    (15,  '#7A1F1F'),  # 12-15% blood red
    (99,  '#2D0A0A'),  # > 15%  near-black
]

CLIMB_CATEGORIES = [
    # (lower_index_inclusive, name, points, badge_colour, fill_colour)
    (80, 'HC',    20, '#212121', '#7A1F1F'),
    (40, 'Cat 1', 10, '#FB8C00', '#FFCC80'),
    (16, 'Cat 2',  5, '#FDD835', '#FFF59D'),
    ( 6, 'Cat 3',  2, '#1E88E5', '#90CAF9'),
    ( 2, 'Cat 4',  1, '#7CB342', '#C5E1A5'),
    ( 0, 'uncat',  0, '#888888', '#E0E0E0'),
]

WAYPOINT_PALETTE = {
    # category: (marker, fill, edge)
    'Start': ('*', '#33aa66', '#006633'),
    'Fuel':  ('o', '#FC8D62', '#A04A2C'),  # Set2 orange
    'Water': ('s', '#66C2A5', '#2E7D5B'),  # Set2 teal
    'Pub':   ('D', '#8DA0CB', '#465B8C'),  # Set2 muted blue
    'POI':   ('^', '#E78AC3', '#9C3E76'),  # Set2 pink
    'Check': ('X', '#cc3333', '#990000'),
}


# ============== HELPERS ==============

def grade_colour(g):
    """Hex colour for a grade percentage (Strava-aligned)."""
    g = abs(g)
    for thr, colour in GRADE_BANDS:
        if g < thr:
            return colour
    return GRADE_BANDS[-1][1]


def categorise(length_km, avg_grade_pct):
    """Return (name, points, badge_colour, fill_colour, index)."""
    idx = length_km * avg_grade_pct
    for thr, name, pts, badge, fill in CLIMB_CATEGORIES:
        if idx >= thr:
            return name, pts, badge, fill, idx
    return 'uncat', 0, '#888888', '#E0E0E0', idx


def median_filter_1d(arr, size=5):
    if size < 2 or len(arr) < size:
        return np.asarray(arr, dtype=float)
    half = size // 2
    out = np.empty(len(arr), dtype=float)
    for i in range(len(arr)):
        out[i] = np.median(arr[max(0, i-half):min(len(arr), i+half+1)])
    return out


def grade_profile(d, e, win_m=50, median_size=5, step_m=10):
    """Return (grid_m, elevation, grade_pct) on a 10m grid with median-filtered elevation."""
    e_filt = median_filter_1d(e, size=median_size)
    grid = np.arange(d[0], d[-1], step_m)
    eg = np.interp(grid, d, e_filt)
    half = max(1, int(win_m / step_m / 2))
    grad = np.zeros_like(eg)
    grad[half:-half] = (eg[2*half:] - eg[:-2*half]) / (2 * half * step_m) * 100
    return grid, eg, grad


# ============== MAIN RENDER ==============

_DATA_SOURCE_BADGES = {
    'hi-fi':        ('HI-FI · 1m lidar @ 5m',         '#2e7d32', 'white'),
    'gpx-forced':   ('GPX only · forced',             '#616161', 'white'),
    'gpx-degraded': ('GPX only · verifier unavailable', '#c62828', 'white'),
}


def render_overview(
    dists,            # 1-D array of cumulative distance in metres
    eles,             # 1-D array of elevation in metres
    climbs,           # list of climb dicts (start_km, end_km, length_m, gain_m,
                      #   avg_grad_pct, max_grad_pct, optional 'cat'/'kom'/'badge'/'fill')
    waypoints,        # list of {km, cat, disp} — cat must be in WAYPOINT_PALETTE
    out_path,
    title='',
    subtitle_extras='',
    figsize=(15, 7.5),
    dpi=120,
    data_source=None, # one of 'hi-fi' / 'gpx-forced' / 'gpx-degraded' / None
    walls=None,       # list of {route_km, peak_pct, length_m} — red ▲ markers
):
    """Render an overview profile chart.

    Layout (2 rows):
        suptitle (figure-level)
        [waypoint lane : top]   icons + labels in their own axis
        [profile       : main]  grade-coloured area + climb info boxes

    Stops legend lives ABOVE the waypoint lane (in the suptitle band) so it
    never collides with markers. Grade legend lives top-right of the profile.
    """

    # Auto-categorise climbs that don't already have a 'cat'
    for c in climbs:
        if 'cat' not in c:
            n, p, b, f, idx = categorise(c['length_m']/1000, c['avg_grad_pct'])
            c.update(cat=n, kom=p, badge=b, fill=f, index=idx)
    total_kom = sum(c.get('kom', 0) for c in climbs)

    grid, eg, grad = grade_profile(dists, eles, win_m=50)
    total_km = dists[-1] / 1000

    # Auto-categorise waypoints into the palette
    for w in waypoints:
        cat = w.get('cat', 'POI')
        if cat not in WAYPOINT_PALETTE:
            cat = 'POI'
        m, fc, ec = WAYPOINT_PALETTE[cat]
        w.setdefault('marker', m)
        w.setdefault('fc', fc)
        w.setdefault('ec', ec)
        w['cat'] = cat

    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.4, 6.0], hspace=0.04)
    ax_w = fig.add_subplot(gs[0])              # waypoint lane (TOP)
    ax = fig.add_subplot(gs[1], sharex=ax_w)   # main profile

    # ===== FIGURE TITLE (suptitle — sits ABOVE all axes) =====
    asc = int(np.sum(np.maximum(0, np.diff(median_filter_1d(eles, 5)))))
    main_title = title or 'Route'
    sub = (f"{total_km:.1f} km · {asc} m gain · "
           f"{total_kom} KOM pts · {len(climbs)} climbs")
    if subtitle_extras:
        sub += f" · {subtitle_extras}"
    fig.suptitle(f"{main_title}\n{sub}", fontsize=12, fontweight='bold', y=0.995)

    # Data-source badge (top-right of figure)
    if data_source in _DATA_SOURCE_BADGES:
        label, bg, fg = _DATA_SOURCE_BADGES[data_source]
        fig.text(
            0.995, 0.965, label,
            ha='right', va='top',
            fontsize=9, fontweight='bold', color=fg,
            bbox=dict(boxstyle='round,pad=0.45', facecolor=bg, edgecolor='none'),
            zorder=10,
        )

    # ===== PROFILE (bottom, the big one) =====
    ele_min = eles.min()
    floor = ele_min - 12
    for i in range(len(grid) - 1):
        ax.fill_between(
            [grid[i]/1000, grid[i+1]/1000], [eg[i], eg[i+1]],
            y2=floor, color=grade_colour(grad[i]), linewidth=0)
    ax.plot(grid/1000, eg, color='black', linewidth=0.8)

    # Climb info boxes — TdF/Veloviewer style
    climb_texts = []
    for c in climbs:
        midk = (c['start_km'] + c['end_km']) / 2
        peak_e = max(np.interp([c['start_km']*1000, c['end_km']*1000], grid, eg))
        lbl = (f"{c['cat']} ({c.get('kom','?')}p)\n"
               f"{c['length_m']:.0f}m × {c['avg_grad_pct']:.1f}% avg\n"
               f"max {c['max_grad_pct']:.1f}%")
        t = ax.annotate(
            lbl, xy=(midk, peak_e), xytext=(midk, peak_e + 45),
            ha='center', fontsize=8, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.35', facecolor=c['fill'],
                      edgecolor=c['badge'], linewidth=1.3),
            arrowprops=dict(arrowstyle='->', color=c['badge'], linewidth=1.1),
        )
        climb_texts.append(t)

    # Wall markers — small red ▲ above the profile at each >10% segment.
    # Compact, doesn't compete with the climb info boxes; ride-wide threat map.
    if walls:
        for wall in walls:
            wk = wall['route_km']
            # Interpolate elevation at the wall location for the marker anchor.
            we = float(np.interp(wk * 1000.0, grid, eg))
            ax.scatter([wk], [we + 18], marker='^', s=70,
                       color='#C8302C', edgecolors='#7A1F1F', linewidths=0.8,
                       zorder=8)
            ax.annotate(
                f"{wall['peak_pct']:.0f}%",
                xy=(wk, we + 18), xytext=(0, 8),
                textcoords='offset points',
                ha='center', va='bottom',
                fontsize=7, fontweight='bold', color='#7A1F1F',
                zorder=8,
            )

    # Vertical leader lines from waypoints down through the profile
    for w in waypoints:
        ax.axvline(w['km'], color=w['fc'], linestyle='--',
                   linewidth=0.9, alpha=0.55, zorder=1)

    ax.set_ylim(floor, eles.max() + 80)
    ax.set_ylabel('Elevation (m)', fontsize=10)
    ax.set_xlabel('Distance (km)', fontsize=10)
    ax.grid(True, alpha=0.25)

    # NOTE: adjustText is intentionally NOT applied to climb boxes.
    # They have a strong semantic anchor (above the climb summit) and adjustText
    # would happily move them DOWN past the peak to avoid other artifacts,
    # producing leader arrows that point upward — counter-intuitive and ugly.
    # If two climbs are too close horizontally, bump the second one's vertical
    # offset rather than letting the layout engine guess.
    if len(climb_texts) > 1:
        # Sort by km, detect overlapping x-ranges, stagger y-offset upward
        sorted_idx = sorted(range(len(climbs)),
                            key=lambda i: (climbs[i]['start_km'] + climbs[i]['end_km']) / 2)
        last_x_end = -1e9
        bump = 0
        for i in sorted_idx:
            c = climbs[i]
            midk = (c['start_km'] + c['end_km']) / 2
            # Approximate label width ~ 5 km of x-axis; collision if too close
            if midk - last_x_end < 4.5:
                bump += 1
                t = climb_texts[i]
                x, y = t.xyann
                t.set_position((x, y + 30 * bump))
            else:
                bump = 0
            last_x_end = midk

    grade_handles = [plt.Rectangle((0, 0), 1, 1, color=c, label=l) for l, c in
                     [('<3%',  '#9ED99E'), ('3–6%',  '#F4C430'),
                      ('6–9%',  '#E8743B'), ('9–12%', '#C8302C'),
                      ('12–15%','#7A1F1F'), ('>15%',  '#2D0A0A')]]
    ax.legend(handles=grade_handles, loc='upper right', fontsize=8,
              title='Grade', framealpha=0.95, ncol=1)

    # ===== WAYPOINT LANE (top) =====
    ax_w.set_facecolor('#fafafa')
    ax_w.set_yticks([])
    ax_w.set_ylim(0, 1)
    ax_w.set_xlim(0, total_km)
    ax_w.tick_params(labelbottom=False, length=0)
    for spine in ('top', 'right', 'left'):
        ax_w.spines[spine].set_visible(False)

    marker_y = 0.78
    wpt_texts = []
    for w in waypoints:
        ax_w.scatter([w['km']], [marker_y], marker=w['marker'], s=170,
                     color=w['fc'], edgecolors=w['ec'], linewidths=1.4, zorder=5)
        ax_w.plot([w['km'], w['km']], [marker_y - 0.05, 0.05],
                  color=w['fc'], linestyle='-', linewidth=0.6, alpha=0.5)
        t = ax_w.text(w['km'], 0.32, f"{w['km']:.1f} km\n{w['disp']}",
                      ha='center', va='top', fontsize=8, color=w['ec'],
                      fontweight='bold')
        wpt_texts.append(t)

    # adjustText: x-first collision avoidance, allow small vertical wiggle
    # for cases where labels are too long to separate horizontally alone.
    if wpt_texts:
        adjust_text(
            wpt_texts, ax=ax_w,
            only_move={'text': 'xy', 'static': 'xy', 'explode': 'xy'},
            expand=(1.25, 1.15),
            force_text=(0.6, 0.3),
            avoid_self=False)

    # Stops legend — placed ABOVE the lane (top of figure, centered),
    # so it never collides with markers.
    seen = set()
    cat_handles = []
    for w in waypoints:
        if w['cat'] in seen:
            continue
        seen.add(w['cat'])
        cat_handles.append(plt.Line2D(
            [0], [0], marker=w['marker'], color='w',
            markerfacecolor=w['fc'], markeredgecolor=w['ec'],
            markersize=10, label=w['cat']))
    if cat_handles:
        fig.legend(handles=cat_handles, loc='upper center',
                   bbox_to_anchor=(0.5, 0.93),
                   fontsize=8, ncol=len(cat_handles),
                   framealpha=0.95, frameon=True)

    plt.savefig(out_path, dpi=dpi, bbox_inches='tight', facecolor='white')
    plt.close()
    return out_path
