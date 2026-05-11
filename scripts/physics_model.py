"""
Physics model for cycling speed/power predictions.

Used by analyse_fit.py and analyse_gpx.py. All constants and rider numbers
load from USER_PROFILE.md via scripts/profile.py — see that module for the
fallback defaults that apply when no profile exists.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from profile import (  # noqa: E402
    FTP,
    MAP_WORKING,
    AC_FRESH_EST,
    NM_PEAK,
    RIDER_WEIGHT_KG,
    BIKE_WEIGHT_KG,
    SYSTEM_WEIGHT_KG,
    CDA_DEFAULT,
    CRR_DEFAULT,
    DRIVETRAIN_EFFICIENCY,
    AIR_DENSITY,
    GRAVITY,
    WHEEL_CIRCUMFERENCE_M,
    power_zone_bounds,
)

# CRR presets — see CLAUDE.md for when each applies. The "default" used by the
# physics functions is `CRR_DEFAULT` (loaded from the profile); these are
# named alternatives the rider can pass explicitly.
CRR_OPTIMAL = 0.0050        # latex/TPU at Silca-optimal pressure
CRR_MID = 0.0055            # intermediate pressure
CRR_OVERPRESSURE = 0.0058   # high pressure above break-point, OR butyl tubes


def predict_speed(power_crank_w, grade_pct, system_weight_kg=SYSTEM_WEIGHT_KG,
                  cda=CDA_DEFAULT, crr=CRR_DEFAULT, eta=DRIVETRAIN_EFFICIENCY,
                  rho=AIR_DENSITY, g=GRAVITY):
    """
    Predict speed (km/h) given crank power (W) and grade (%).

    Solves: P_wheel = (½ρCdA·v² + CRR·m·g + m·g·sin(θ)) · v
    where P_wheel = P_crank × η_drive
    """
    p_wheel = power_crank_w * eta
    theta = np.arctan(grade_pct / 100)
    sin_theta = np.sin(theta)

    a = 0.5 * rho * cda
    b = (crr + sin_theta) * system_weight_kg * g
    c = -p_wheel

    # Cubic in v: a·v³ + b·v + c = 0
    coeffs = [a, 0, b, c]
    roots = np.roots(coeffs)
    real_roots = [r.real for r in roots if np.isreal(r) and r.real > 0]
    if not real_roots:
        return 0.0
    return min(real_roots) * 3.6  # m/s → km/h


def predict_power(speed_kmh, grade_pct, system_weight_kg=SYSTEM_WEIGHT_KG,
                  cda=CDA_DEFAULT, crr=CRR_DEFAULT, eta=DRIVETRAIN_EFFICIENCY,
                  rho=AIR_DENSITY, g=GRAVITY):
    """
    Predict required crank power (W) given target speed (km/h) and grade (%).
    """
    v = speed_kmh / 3.6
    theta = np.arctan(grade_pct / 100)
    sin_theta = np.sin(theta)

    p_wheel = (0.5 * rho * cda * v**2 + (crr + sin_theta) * system_weight_kg * g) * v
    return p_wheel / eta


def speed_at_cadence_rpm(cadence_rpm, gear_ratio, wheel_circ_m=WHEEL_CIRCUMFERENCE_M):
    """Speed (km/h) for a given cadence and gear ratio."""
    return cadence_rpm * gear_ratio * wheel_circ_m * 60 / 1000


def power_uncertainty_envelope(predicted_speed_kmh, grade_pct):
    """
    Return ± uncertainty in km/h for a predicted speed.

    Combined uncertainty from CdA (±7%), CRR (±10%), power meter (±4%), weight (±1%).
    """
    if grade_pct >= 5:
        return 1.5  # climbs dominated by gravity, model is more accurate
    elif grade_pct >= 1:
        return 1.7
    else:
        return 2.0  # flat is dominated by CdA uncertainty


def vam_at_power(power_crank_w, grade_pct, system_weight_kg=SYSTEM_WEIGHT_KG,
                 **kwargs):
    """Vertical ascent metres per hour (VAM) at given power and grade."""
    speed_kmh = predict_speed(power_crank_w, grade_pct, system_weight_kg, **kwargs)
    return speed_kmh * 1000 * grade_pct / 100  # m/h


def power_for_60rpm_in_lowest_gear(grade_pct, lowest_ratio=30/32,
                                   system_weight_kg=SYSTEM_WEIGHT_KG, **kwargs):
    """
    Power needed to maintain 60 rpm (the minimum sustainable cadence under load)
    in the lowest gear (default 30×32 = ratio 0.94) on a given grade.

    This is the 'survival number' for steep climbs.
    """
    speed_kmh = speed_at_cadence_rpm(60, lowest_ratio)
    return predict_power(speed_kmh, grade_pct, system_weight_kg, **kwargs)


# Power zones — materialised from the profile's FTP/MAP/AC/NM.
ZONES = power_zone_bounds()


def zone_for_power(power_w):
    """Return the primary zone name for a given power."""
    for name, lo, hi in ZONES:
        if lo <= power_w <= hi:
            return name
    if power_w < ZONES[0][1]:
        return ZONES[0][0]
    return ZONES[-1][0]


if __name__ == '__main__':
    # Self-test: print climb predictions at FTP and MAP for a 9% grade,
    # which is roughly the average of a Cat-3 ascent.
    print(f'Sample climb @ 9% grade, system weight {SYSTEM_WEIGHT_KG:.1f} kg, '
          f'CdA {CDA_DEFAULT:.2f}, CRR {CRR_DEFAULT:.4f}:')
    print()
    for power, label in [(FTP, f'FTP {FTP}W'), (MAP_WORKING, f'MAP {MAP_WORKING}W')]:
        speed = predict_speed(power, 9.0)
        time_min = 1.4 / speed * 60
        vam = vam_at_power(power, 9.0)
        print(f'  {label:20s}  {speed:5.2f} km/h  {time_min:4.1f} min  VAM {vam:.0f} m/h')

    print()
    print('Survival check at 16% pitch:')
    survival_p = power_for_60rpm_in_lowest_gear(16.0)
    print(f'  Power for 60 rpm in 30×32: {survival_p:.0f} W ({survival_p/FTP*100:.0f}% FTP)')
