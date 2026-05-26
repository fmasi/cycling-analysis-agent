"""
Tyre pressure calculator — bike-aware.

Two backends:

1. **Silca extrapolation** (default for bikes without USER_PROFILE-stored
   pressures, e.g. the Tripster). Uses Silca's road-tyre baselines and the
   empirical slope of ~0.67 psi per 1% F/R split shift per wheel.

2. **Canonical lookup** (for bikes whose USER_PROFILE entry contains
   `tyre_pressure_psi:`). Returns the rider's validated targets directly,
   with a small Berto-derived weight delta applied if the requested system
   weight differs from the bike's default. This path also surfaces validation
   provenance and a Berto-15% reference number for comparison.

Why two paths: web research (2026-05-15) confirmed that no published model
handles the Brompton G's 20"/406 BSD + 54 mm tyre + 40/60 split combination —
every calculator extrapolates from 700C empirical data. For bikes flagged
`unvalidated_by_model: true`, the canonical lookup is the only honest output;
formulas would dress up the wrong number.

Usage:
    python scripts/tyre_pressure.py
    python scripts/tyre_pressure.py --bike tripster
    python scripts/tyre_pressure.py --bike tripster --surface worn
    python scripts/tyre_pressure.py --bike brompton_g --surface gravel
    python scripts/tyre_pressure.py --bike brompton_g --system-weight 99
    python scripts/tyre_pressure.py --system-weight 90.1 --front-pct 40
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bike_config import load_bike, BikeConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Silca path (road tyres — Tripster and similar)
# ---------------------------------------------------------------------------

# Silca baselines at 90 kg, 31mm tyre, mid-range tubeless/latex, moderate group ride.
# Format: (front_psi, rear_psi) at 48/52 (Silca's "Road" preset).
SILCA_BASELINE_90KG = {
    'new':  (67.5, 69.5),   # New Pavement
    'worn': (64.0, 65.5),   # Worn Pavement / Some Cracks
    'poor': (58.5, 60.0),   # Poor Pavement / Chipseal
}

SILCA_SURFACE_NAMES = {
    'new':  'New Pavement',
    'worn': 'Worn Pavement (typical road)',
    'poor': 'Poor Pavement / Chipseal',
}

# Slope: psi change per 1% weight-distribution shift, per wheel.
PSI_PER_PERCENT_SHIFT = 0.67

# Silca's reference Road preset is 48/52 (front share = 48%).
SILCA_REFERENCE_FRONT_PCT = 48


def silca_pressures_at_split(surface, front_pct=SILCA_REFERENCE_FRONT_PCT,
                              baseline_kg=90, actual_kg=90.0):
    """Linearly extrapolate Silca baselines for arbitrary F/R split + weight."""
    base_f, base_r = SILCA_BASELINE_90KG[surface]
    shift_pct = SILCA_REFERENCE_FRONT_PCT - front_pct
    front_psi = base_f - shift_pct * PSI_PER_PERCENT_SHIFT
    rear_psi = base_r + shift_pct * PSI_PER_PERCENT_SHIFT
    if actual_kg != baseline_kg:
        weight_factor = actual_kg / baseline_kg
        front_psi *= (1 + (weight_factor - 1) * (front_pct / 48))
        rear_psi *= (1 + (weight_factor - 1) * ((100 - front_pct) / 52))
    return round(front_psi, 0), round(rear_psi, 0)


# ---------------------------------------------------------------------------
# Canonical lookup path (Brompton G and any future bike with stored targets)
# ---------------------------------------------------------------------------

# Berto polynomial dP/dL slope at 54 mm tyre width: 600 / W^2 psi per lb axle load.
# At W=54mm: 0.206 psi/lb = 0.454 psi/kg of axle load. Used as a local
# sensitivity around the rider-validated baseline — NOT to compute absolute psi
# from scratch (Berto's chart is 700C only; see unvalidated_by_model note).
PSI_PER_KG_AXLE_DELTA_54MM = 0.45


def berto_reference_psi(width_mm, axle_load_kg):
    """Berto polynomial: P = 600·L/W^2 + 0.75·W − 25, L in lbs, W in mm.
    Reference only — not trustworthy for non-700C wheels.
    """
    load_lbs = axle_load_kg * 2.20462
    return round(600.0 * load_lbs / (width_mm ** 2) + 0.75 * width_mm - 25.0, 0)


def lookup_pressures(bike: BikeConfig, surface: str, system_kg: float,
                     front_pct: float):
    """Read canonical pressures from bike.tyre_pressure_psi and apply weight delta."""
    if surface not in bike.tyre_pressure_psi:
        raise ValueError(
            f"Surface '{surface}' not in tyre_pressure_psi for '{bike.slug}'. "
            f"Available: {list(bike.tyre_pressure_psi)}"
        )
    entry = bike.tyre_pressure_psi[surface]
    base_f = float(entry['front'])
    base_r = float(entry['rear'])

    weight_delta = system_kg - bike.system_weight_kg_default
    front_share = front_pct / 100.0
    rear_share = 1.0 - front_share

    front_adj = base_f + weight_delta * front_share * PSI_PER_KG_AXLE_DELTA_54MM
    rear_adj = base_r + weight_delta * rear_share * PSI_PER_KG_AXLE_DELTA_54MM

    return {
        'front_psi': round(front_adj, 0),
        'rear_psi': round(rear_adj, 0),
        'base_front': base_f,
        'base_rear': base_r,
        'weight_delta_kg': weight_delta,
        'rider_validated': bool(entry.get('rider_validated', False)),
        'rider_validated_date': entry.get('rider_validated_date'),
        'silca_raw_front': entry.get('silca_raw_front'),
        'silca_raw_rear': entry.get('silca_raw_rear'),
        'source': entry.get('source', ''),
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def print_silca_table(bike, system_kg, front_pct, surfaces):
    print('Tyre pressure targets (Silca extrapolation)')
    print(f'  Bike:          {bike.name}')
    print(f'  System weight: {system_kg} kg')
    print(f'  F/R split:     {front_pct:.0f}/{100 - front_pct:.0f}')
    print()
    print(f"{'Surface':<35} {'Front':>6} {'Rear':>6}  {'Delta':>6}")
    print('-' * 60)
    for s in surfaces:
        f, r = silca_pressures_at_split(s, front_pct, actual_kg=system_kg)
        delta = r - f
        print(f"{SILCA_SURFACE_NAMES[s]:<35} {int(f):>4} psi {int(r):>4} psi  {int(delta):>4} psi")
    print()
    print('Notes:')
    print('  - TPU tubes lose ~3-5 psi/week; check before each ride')
    print('  - Stop dropping pressure if cornering feels squirmy or pinch-flat risk rises')
    print('  - Front/rear delta scales with how far the F/R split is from 48/52')


def print_lookup_table(bike, system_kg, front_pct, surfaces):
    print('Tyre pressure targets (canonical lookup — USER_PROFILE.md)')
    print(f'  Bike:          {bike.name}')
    print(f'  Tyre:          {bike.tyres.get("model", "?")} {bike.tyres.get("size_etrto", "")}')
    print(f'  System weight: {system_kg} kg  (default {bike.system_weight_kg_default} kg)')
    print(f'  F/R split:     {front_pct:.0f}/{100 - front_pct:.0f}')
    if bike.unvalidated_by_model:
        print(f'  Model status:  ⚠ unvalidated by any published model')
        print(f'                 (no calculator handles {bike.tyres.get("size_etrto", "this size")} reliably)')
    print()
    print(f"{'Surface':<22} {'Front':>6} {'Rear':>6} {'Delta':>6}  {'Validated':>10}  {'Berto-ref (F/R)':>16}")
    print('-' * 80)

    # Width for Berto reference — strip "54-406" → 54
    width_mm = None
    size_str = str(bike.tyres.get("size_etrto", ""))
    if "-" in size_str:
        try:
            width_mm = int(size_str.split("-")[0])
        except ValueError:
            width_mm = None

    front_share = front_pct / 100.0
    rear_share = 1.0 - front_share

    for s in surfaces:
        r = lookup_pressures(bike, s, system_kg, front_pct)
        delta = r['rear_psi'] - r['front_psi']
        validated = "yes" if r['rider_validated'] else "no"
        if r['rider_validated_date']:
            validated = f"yes ({r['rider_validated_date']})"

        if width_mm is not None:
            berto_f = berto_reference_psi(width_mm, system_kg * front_share)
            berto_r = berto_reference_psi(width_mm, system_kg * rear_share)
            berto_str = f"{int(berto_f)}/{int(berto_r)}"
        else:
            berto_str = "—"

        print(
            f"{s:<22} {int(r['front_psi']):>4} psi {int(r['rear_psi']):>4} psi {int(delta):>4} psi  "
            f"{validated:>10}  {berto_str:>16}"
        )

    print()
    print('Notes:')
    if bike.unvalidated_by_model:
        print('  - No published pressure model fits this bike — values are rider-validated')
        print('    where flagged, reference targets otherwise. Adjust from on-road feel.')
        if bike.unvalidated_by_model_source:
            print(f'  - Provenance: {bike.unvalidated_by_model_source[:100]}…')
    if bike.tyre_pressure_uncertainty_psi:
        print(f'  - Uncertainty: ±{bike.tyre_pressure_uncertainty_psi:.0f} psi (rider tolerance)')
    print('  - Berto-15% reference column shown for comparison only — 700C-derived, off for 406 BSD')
    if any(not lookup_pressures(bike, s, system_kg, front_pct)['rider_validated']
           for s in surfaces):
        print('  - Unvalidated surfaces: ride them, log feel, refine the numbers in USER_PROFILE.md')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Tyre pressure calculator (bike-aware: Silca for road tyres, canonical lookup otherwise)')
    parser.add_argument('--bike', default=None,
                        help='Bike slug (default: default_bike from USER_PROFILE.md)')
    parser.add_argument('--system-weight', type=float, default=None,
                        help='System weight kg; defaults to bike.system_weight_kg_default')
    parser.add_argument('--front-pct', type=float, default=None,
                        help='Front wheel weight percentage; defaults to bike fr_split front portion')
    parser.add_argument('--surface', default='all',
                        help='Surface key; "all" lists every supported surface for the bike')
    args = parser.parse_args()

    bike = load_bike(slug=args.bike)

    system_kg = args.system_weight if args.system_weight is not None else bike.system_weight_kg_default

    if args.front_pct is not None:
        front_pct = args.front_pct
    elif bike.fr_split.upper() == "TBD":
        print(f"warning: bike '{bike.slug}' has fr_split=TBD — using Silca default 48",
              file=sys.stderr)
        front_pct = 48.0
    else:
        front_pct = float(bike.fr_split.split("/")[0])

    # Route to the right backend.
    if bike.tyre_pressure_psi:
        valid_surfaces = list(bike.tyre_pressure_psi.keys())
        if args.surface == 'all':
            surfaces = valid_surfaces
        elif args.surface in valid_surfaces:
            surfaces = [args.surface]
        else:
            print(
                f"error: surface '{args.surface}' not available for '{bike.slug}'. "
                f"Choose from: {valid_surfaces} or 'all'",
                file=sys.stderr,
            )
            sys.exit(2)
        print_lookup_table(bike, system_kg, front_pct, surfaces)
    else:
        valid_surfaces = list(SILCA_BASELINE_90KG.keys())
        if args.surface == 'all':
            surfaces = valid_surfaces
        elif args.surface in valid_surfaces:
            surfaces = [args.surface]
        else:
            print(
                f"error: surface '{args.surface}' not in Silca categories for '{bike.slug}'. "
                f"Choose from: {valid_surfaces} or 'all'",
                file=sys.stderr,
            )
            sys.exit(2)
        print_silca_table(bike, system_kg, front_pct, surfaces)


if __name__ == '__main__':
    main()
