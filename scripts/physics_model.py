"""
Physics model for cycling speed/power predictions.

Used by analyse_fit.py and analyse_gpx.py. All constants and rider numbers
load from USER_PROFILE.md via scripts/profile.py — see that module for the
fallback defaults that apply when no profile exists.
"""

import math
import sys
from pathlib import Path
from typing import Optional

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
from bike_config import BikeConfig, load_bike

# CRR presets — see CLAUDE.md for when each applies. The "default" used by the
# physics functions is `CRR_DEFAULT` (loaded from the profile); these are
# named alternatives the rider can pass explicitly.
CRR_OPTIMAL = 0.0050        # latex/TPU at Silca-optimal pressure
CRR_MID = 0.0055            # intermediate pressure
CRR_OVERPRESSURE = 0.0058   # high pressure above break-point, OR butyl tubes


def predict_speed(
    power_crank_w: float,
    grade_pct: float,
    *,
    bike: BikeConfig,
    surface: str,
    system_weight_kg: float,
    rho: float = AIR_DENSITY,
    g: float = GRAVITY,
) -> float:
    """Speed in km/h that the given rider power produces on the given bike+surface+grade.

    All bike-specific physics (CdA, CRR, drivetrain efficiency) come from the BikeConfig.

    Solves: P_wheel = (½ρCdA·v² + CRR·m·g + m·g·sin(θ)) · v
    where P_wheel = P_crank × η_drive
    """
    crr = bike.crr_by_surface[surface]
    cda = bike.cda
    eta = bike.drivetrain_efficiency
    p_wheel = power_crank_w * eta
    theta = math.atan(grade_pct / 100.0)

    # Solve p_wheel = (0.5 * rho * cda * v^2 + crr * m * g + m * g * sin(theta)) * v
    # Iteratively (bisection) for v in m/s.
    lo, hi = 0.01, 30.0  # m/s
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        rhs = (0.5 * rho * cda * mid * mid + crr * system_weight_kg * g + system_weight_kg * g * math.sin(theta)) * mid
        if rhs < p_wheel:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) * 3.6  # m/s → km/h


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
    speed_kmh = predict_speed_legacy(power_crank_w, grade_pct, system_weight_kg, **kwargs)
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


def predict_speed_legacy(power_crank_w, grade_pct, system_weight_kg=None, cda=None,
                          crr=None, eta=None, rho=AIR_DENSITY, g=GRAVITY):
    """Deprecated: pre-bike-aware signature. Routes through the default bike.

    Kept for backwards compatibility with analyse_gpx.py and analyse_fit.py until
    those callers migrate to the new bike-aware predict_speed() signature (Tasks 8/9).
    """
    bike = load_bike()  # default
    sw = system_weight_kg if system_weight_kg is not None else bike.system_weight_kg_default
    # If caller passed explicit cda/crr/eta overrides, build a temporary BikeConfig clone.
    # Otherwise delegate to the bike's own values.
    if cda is not None or crr is not None or eta is not None:
        import dataclasses
        surface = bike.surfaces_supported[0]
        crr_val = crr if crr is not None else bike.crr_by_surface[surface]
        override_bike = dataclasses.replace(
            bike,
            cda=cda if cda is not None else bike.cda,
            drivetrain_efficiency=eta if eta is not None else bike.drivetrain_efficiency,
            crr_by_surface={**bike.crr_by_surface, surface: crr_val},
        )
        return predict_speed(power_crank_w, grade_pct, bike=override_bike,
                             surface=surface, system_weight_kg=sw, rho=rho, g=g)
    return predict_speed(power_crank_w, grade_pct, bike=bike,
                         surface=bike.surfaces_supported[0],
                         system_weight_kg=sw, rho=rho, g=g)


if __name__ == '__main__':
    # Self-test: print climb predictions at FTP and MAP for a 9% grade,
    # which is roughly the average of a Cat-3 ascent.
    print(f'Sample climb @ 9% grade, system weight {SYSTEM_WEIGHT_KG:.1f} kg, '
          f'CdA {CDA_DEFAULT:.2f}, CRR {CRR_DEFAULT:.4f}:')
    print()
    for power, label in [(FTP, f'FTP {FTP}W'), (MAP_WORKING, f'MAP {MAP_WORKING}W')]:
        speed = predict_speed_legacy(power, 9.0)
        time_min = 1.4 / speed * 60
        vam = vam_at_power(power, 9.0)
        print(f'  {label:20s}  {speed:5.2f} km/h  {time_min:4.1f} min  VAM {vam:.0f} m/h')

    print()
    print('Survival check at 16% pitch:')
    survival_p = power_for_60rpm_in_lowest_gear(16.0)
    print(f'  Power for 60 rpm in 30×32: {survival_p:.0f} W ({survival_p/FTP*100:.0f}% FTP)')
