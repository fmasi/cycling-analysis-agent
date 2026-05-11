"""
Silca-extrapolated tyre pressure calculator.

Uses the empirical slope measured from running Silca's calculator at multiple
weight-distribution points: ~0.67 psi per 1% weight-distribution shift per
wheel.

Many riders measure an F/R weight split outside Silca's preset range
(50/50 to 46.5/53.5). This script extrapolates from Silca's closest preset
using the empirical slope so the calculator works for any measured split.

Defaults (system weight, F/R split) come from USER_PROFILE.md via
scripts/profile.py. Override on the command line for one-off calculations.

Usage:
    python scripts/tyre_pressure.py
    python scripts/tyre_pressure.py --system-weight 90.1 --front-pct 40
    python scripts/tyre_pressure.py --surface worn
    python scripts/tyre_pressure.py --surface poor
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from profile import SYSTEM_WEIGHT_KG, FR_SPLIT_FRONT_PCT  # noqa: E402


# Silca baselines at 90 kg, 31mm tyre, mid-range tubeless/latex, moderate group ride.
# Format: (front_psi, rear_psi) at 48/52 (Silca's "Road" preset).
SILCA_BASELINE_90KG = {
    'new':  (67.5, 69.5),   # New Pavement
    'worn': (64.0, 65.5),   # Worn Pavement / Some Cracks
    'poor': (58.5, 60.0),   # Poor Pavement / Chipseal
}

# Slope: psi change per 1% weight-distribution shift, per wheel.
# Front shifts opposite direction to rear; this is the magnitude.
PSI_PER_PERCENT_SHIFT = 0.67

# Silca's reference Road preset is 48/52 (front share = 48%).
SILCA_REFERENCE_FRONT_PCT = 48


def pressures_at_split(surface, front_pct=SILCA_REFERENCE_FRONT_PCT,
                       baseline_kg=90, actual_kg=SYSTEM_WEIGHT_KG):
    """
    Compute (front, rear) psi for the given F/R split and surface.

    Linearly extrapolates from Silca's 48/52 baseline using the empirical slope.
    Linearly scales for system weight delta (Silca uses ~1 psi per 1.5 kg per wheel).
    """
    base_f, base_r = SILCA_BASELINE_90KG[surface]
    shift_pct = SILCA_REFERENCE_FRONT_PCT - front_pct  # positive = more rear-biased
    front_psi = base_f - shift_pct * PSI_PER_PERCENT_SHIFT
    rear_psi = base_r + shift_pct * PSI_PER_PERCENT_SHIFT

    # Weight scaling — small effect, but apply for completeness.
    if actual_kg != baseline_kg:
        weight_factor = actual_kg / baseline_kg
        front_psi *= (1 + (weight_factor - 1) * (front_pct / 48))
        rear_psi *= (1 + (weight_factor - 1) * ((100 - front_pct) / 52))

    return round(front_psi, 0), round(rear_psi, 0)


def all_surfaces(front_pct=SILCA_REFERENCE_FRONT_PCT, system_kg=SYSTEM_WEIGHT_KG):
    """Return targets for all three surface conditions."""
    out = {}
    for surface in ['new', 'worn', 'poor']:
        f, r = pressures_at_split(surface, front_pct, actual_kg=system_kg)
        out[surface] = (int(f), int(r))
    return out


def main():
    parser = argparse.ArgumentParser(
        description='Tyre pressure calculator (Silca-extrapolated)')
    parser.add_argument('--system-weight', type=float, default=SYSTEM_WEIGHT_KG,
                        help=f'System weight in kg (default from profile: {SYSTEM_WEIGHT_KG})')
    parser.add_argument('--front-pct', type=float, default=FR_SPLIT_FRONT_PCT,
                        help=f'Front wheel weight percentage (default from profile: {FR_SPLIT_FRONT_PCT})')
    parser.add_argument('--surface', choices=['new', 'worn', 'poor', 'all'],
                        default='all',
                        help='Surface condition (default: all)')
    args = parser.parse_args()

    print('Tyre pressure targets')
    print(f'  System weight: {args.system_weight} kg')
    print(f'  F/R split:     {args.front_pct:.0f}/{100-args.front_pct:.0f}')
    print()
    print(f"{'Surface':<35} {'Front':>6} {'Rear':>6}  {'Delta':>6}")
    print('-' * 60)

    surface_names = {
        'new':  'New Pavement',
        'worn': 'Worn Pavement (typical road)',
        'poor': 'Poor Pavement / Chipseal',
    }
    surfaces = ['new', 'worn', 'poor'] if args.surface == 'all' else [args.surface]
    for s in surfaces:
        f, r = pressures_at_split(s, args.front_pct, actual_kg=args.system_weight)
        delta = r - f
        print(f"{surface_names[s]:<35} {int(f):>4} psi {int(r):>4} psi  {int(delta):>4} psi")

    print()
    print('Notes:')
    print('  - TPU tubes lose ~3-5 psi/week; check before each ride')
    print('  - Stop dropping pressure if cornering feels squirmy or pinch-flat risk rises')
    print('  - Front/rear delta scales with how far the F/R split is from 48/52')


if __name__ == '__main__':
    main()
