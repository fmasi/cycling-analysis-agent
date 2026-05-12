"""
Silca-extrapolated tyre pressure calculator.

Uses the empirical slope measured from running Silca's calculator at multiple
weight-distribution points: ~0.67 psi per 1% weight-distribution shift per
wheel.

Many riders measure an F/R weight split outside Silca's preset range
(50/50 to 46.5/53.5). This script extrapolates from Silca's closest preset
using the empirical slope so the calculator works for any measured split.

Defaults (system weight, F/R split) come from the bike's BikeConfig block in
USER_PROFILE.md. Override on the command line for one-off calculations.

Usage:
    python scripts/tyre_pressure.py
    python scripts/tyre_pressure.py --bike tripster
    python scripts/tyre_pressure.py --bike tripster --surface worn
    python scripts/tyre_pressure.py --bike brompton_g --surface poor
    python scripts/tyre_pressure.py --system-weight 90.1 --front-pct 40
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bike_config import load_bike  # noqa: E402


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
                       baseline_kg=90, actual_kg=90.0):
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


def all_surfaces(front_pct=SILCA_REFERENCE_FRONT_PCT, system_kg=90.0):
    """Return targets for all three surface conditions."""
    out = {}
    for surface in ['new', 'worn', 'poor']:
        f, r = pressures_at_split(surface, front_pct, actual_kg=system_kg)
        out[surface] = (int(f), int(r))
    return out


def main():
    parser = argparse.ArgumentParser(
        description='Tyre pressure calculator (Silca-extrapolated)')
    parser.add_argument('--bike', default=None,
                        help='Bike slug (default: default_bike from USER_PROFILE.md)')
    parser.add_argument('--system-weight', type=float, default=None,
                        help='System weight kg; defaults to bike.system_weight_kg_default')
    parser.add_argument('--front-pct', type=float, default=None,
                        help='Front wheel weight percentage; defaults to bike fr_split front portion')
    parser.add_argument('--surface', choices=['new', 'worn', 'poor', 'all'],
                        default='all',
                        help='Silca surface category (default: all)')
    args = parser.parse_args()

    bike = load_bike(slug=args.bike)

    system_kg = args.system_weight if args.system_weight is not None else bike.system_weight_kg_default

    unvalidated = False
    if args.front_pct is not None:
        front_pct = args.front_pct
    elif bike.fr_split.upper() == "TBD":
        print(f"warning: bike '{bike.slug}' has fr_split=TBD — using Silca default 48",
              file=sys.stderr)
        front_pct = 48.0
        unvalidated = True
    else:
        # Parse "40/60" → 40.0
        front_pct = float(bike.fr_split.split("/")[0])

    if unvalidated or bike.slug == "brompton_g":
        print("NOTE: pressures are indicative — not yet Silca-validated for this bike.",
              file=sys.stderr)
        print("Run the agent-driven Silca lookup (see docs/prompts/silca-pressure-lookup.md).",
              file=sys.stderr)

    print('Tyre pressure targets')
    print(f'  Bike:          {bike.name}')
    print(f'  System weight: {system_kg} kg')
    print(f'  F/R split:     {front_pct:.0f}/{100 - front_pct:.0f}')
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
        f, r = pressures_at_split(s, front_pct, actual_kg=system_kg)
        delta = r - f
        print(f"{surface_names[s]:<35} {int(f):>4} psi {int(r):>4} psi  {int(delta):>4} psi")

    print()
    print('Notes:')
    print('  - TPU tubes lose ~3-5 psi/week; check before each ride')
    print('  - Stop dropping pressure if cornering feels squirmy or pinch-flat risk rises')
    print('  - Front/rear delta scales with how far the F/R split is from 48/52')


if __name__ == '__main__':
    main()
