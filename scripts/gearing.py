"""Cadence and gear-selection maths for derailleur bikes.

Pure functions: no I/O, no profile loading. Used by analyse_gpx (and later
analyse_climbs) to suggest a gear + cadence for a target speed on a climb.
"""
from typing import Optional, Tuple

CADENCE_MIN_RPM = 50.0   # floor for sustainable pedalling
CADENCE_MAX_RPM = 110.0  # ceiling before technique breaks down


def cadence_rpm(speed_kmh: float, chainring_t: int, cog_t: int,
                wheel_circ_m: float) -> float:
    """Pedal cadence (rpm) to hold speed_kmh in the given gear.

    development (m per crank rev) = wheel_circ_m * chainring_t / cog_t
    rpm = (speed in m/min) / development
    """
    if speed_kmh <= 0 or chainring_t <= 0 or cog_t <= 0 or wheel_circ_m <= 0:
        return 0.0
    speed_m_min = speed_kmh * 1000.0 / 60.0
    development_m = wheel_circ_m * chainring_t / cog_t
    return speed_m_min / development_m


def suggest_gear(speed_kmh: float, bike, prefer_rpm: float = 70.0
                 ) -> Optional[Tuple[int, int, float]]:
    """Pick (chainring_t, cog_t, rpm) whose cadence is closest to prefer_rpm.

    Prefers gears giving a plausible cadence (50-110 rpm); if none qualify,
    returns the overall closest. Returns None if the bike has no gearing.
    """
    gearing = getattr(bike, "gearing", None)
    if not gearing:
        return None
    chainrings = gearing["chainrings_t"]
    cogs = gearing["cassette_t"]

    in_range = []
    all_combos = []
    for cr in chainrings:
        for cog in cogs:
            rpm = cadence_rpm(speed_kmh, cr, cog, bike.wheel_circ_m)
            err = abs(rpm - prefer_rpm)
            all_combos.append((err, cr, cog, rpm))
            if CADENCE_MIN_RPM <= rpm <= CADENCE_MAX_RPM:
                in_range.append((err, cr, cog, rpm))

    pool = in_range if in_range else all_combos
    if not pool:
        return None
    pool.sort(key=lambda t: t[0])
    _err, cr, cog, rpm = pool[0]
    return (cr, cog, rpm)
