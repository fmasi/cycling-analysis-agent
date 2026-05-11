"""
Training load projector — CTL/ATL/TSB forecast.

Usage:
    python scripts/training_load.py --ctl 42 --atl 43 --plan 60,60,20,150,0
    python scripts/training_load.py --ctl 42 --atl 43 --plan 60,60,20,150,0 --labels Wed,Thu,Fri,Sat,Sun

Conventions (matches TrainingPeaks display):
    - CTL = 42-day EMA of daily TSS (exponential form, 1 - exp(-1/42))
    - ATL = 7-day EMA  (1 - exp(-1/7))
    - TSB shown on day N = CTL(end of day N-1) - ATL(end of day N-1)
      i.e. the "form" you carry INTO day N's training, before day N's TSS is
      applied. This matches what TP displays (tooltip: "yesterday's fitness
      minus yesterday's fatigue").

    --ctl and --atl are end-of-day values for the day BEFORE the plan starts.
    Typically: the values TP shows you tonight, after logging today's ride.
    The first row of the plan is therefore tomorrow.
"""

import argparse
import math


CTL_DECAY_DAYS = 42
ATL_DECAY_DAYS = 7

CTL_K = 1 - math.exp(-1 / CTL_DECAY_DAYS)  # ≈ 0.02347
ATL_K = 1 - math.exp(-1 / ATL_DECAY_DAYS)  # ≈ 0.13307


def project(ctl_start, atl_start, daily_tss):
    """
    Project CTL/ATL/TSB forward from a given starting state.

    TSB for each day uses TP's lag-1 convention: end-of-previous-day CTL−ATL.
    On day N this is the form the athlete enters training with, before any
    TSS is added that day.

    Args:
        ctl_start: End-of-day CTL for the day BEFORE the plan's first day.
        atl_start: End-of-day ATL for the day BEFORE the plan's first day.
        daily_tss: Iterable of TSS values, one per planned day.

    Returns list of (day_index, tss, ctl_eod, atl_eod, tsb_entry) tuples.
    """
    out = []
    ctl, atl = ctl_start, atl_start
    for i, tss in enumerate(daily_tss):
        tsb_entry = ctl - atl
        ctl = ctl + (tss - ctl) * CTL_K
        atl = atl + (tss - atl) * ATL_K
        out.append((i, tss, ctl, atl, tsb_entry))
    return out


def assess_tsb(tsb):
    """Categorical interpretation of TSB."""
    if tsb > 15:
        return 'detraining risk (long periods)'
    elif tsb >= 5:
        return 'fresh / tapering'
    elif tsb >= -5:
        return 'balanced'
    elif tsb >= -10:
        return 'mild productive fatigue'
    elif tsb >= -20:
        return 'productive fatigue (good adaptation)'
    elif tsb >= -25:
        return 'high fatigue, monitor'
    else:
        return 'OVERREACHING — high illness/injury risk'


def main():
    p = argparse.ArgumentParser(description='Training load (CTL/ATL/TSB) projector')
    p.add_argument('--ctl', type=float, required=True,
                   help='Starting CTL (end-of-day BEFORE the plan begins)')
    p.add_argument('--atl', type=float, required=True,
                   help='Starting ATL (end-of-day BEFORE the plan begins)')
    p.add_argument('--plan', required=True,
                   help='Comma-separated daily TSS values, e.g. 60,60,20,150,0')
    p.add_argument('--labels', help='Comma-separated day labels (optional)')
    args = p.parse_args()

    daily_tss = [float(x) for x in args.plan.split(',')]
    labels = args.labels.split(',') if args.labels else [f'Day {i+1}' for i in range(len(daily_tss))]

    if len(labels) != len(daily_tss):
        print('ERROR: --labels count must match --plan count')
        return

    print(f'Starting state (end-of-day before plan): CTL {args.ctl:.1f}, '
          f'ATL {args.atl:.1f}, TSB {args.ctl - args.atl:+.1f}')
    print('TSB column = TP convention: form entering that day (yesterday CTL−ATL).')
    print()
    print(f"{'Day':<15} {'TSS':>6} {'CTL_eod':>8} {'ATL_eod':>8} {'TSB_entry':>10}  {'State (entry)'}")
    print('-' * 85)
    proj = project(args.ctl, args.atl, daily_tss)
    for label, (i, tss, ctl, atl, tsb) in zip(labels, proj):
        state = assess_tsb(tsb)
        print(f'{label:<15} {tss:>6.0f} {ctl:>8.1f} {atl:>8.1f} {tsb:>+10.1f}  {state}')

    total_tss = sum(daily_tss)
    print()
    print(f'Total TSS: {total_tss:.0f}')
    if total_tss > 280:
        print('  ⚠️  >280 TSS in a single week — should be followed by recovery week')
    elif total_tss < 200:
        print('  ⚠️  <200 TSS in a week — below target band of 200-260 at CTL 42')
    else:
        print('  ✅ Within target band 200-260 TSS/week for current CTL')


if __name__ == '__main__':
    main()
