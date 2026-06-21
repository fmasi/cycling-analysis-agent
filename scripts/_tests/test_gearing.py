import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gearing import cadence_rpm, suggest_gear


class FakeBike:
    wheel_circ_m = 2.155
    gearing = {
        "chainrings_t": [30, 39, 50],
        "cassette_t": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32],
    }


class NoGearBike:
    wheel_circ_m = 1.59
    gearing = None


def test_cadence_rpm_known_value():
    # 30T x 15T on 2.155m wheel = development 4.31 m/rev.
    # At 15 km/h = 250 m/min -> 250/4.31 = ~58 rpm.
    rpm = cadence_rpm(15.0, 30, 15, 2.155)
    assert abs(rpm - 58.0) < 1.0


def test_cadence_rpm_zero_speed():
    assert cadence_rpm(0.0, 30, 15, 2.155) == 0.0


def test_suggest_gear_targets_prefer_rpm():
    # At 12 km/h (a ~10% climb speed), prefer 70 rpm.
    bike = FakeBike()
    cr, cog, rpm = suggest_gear(12.0, bike, prefer_rpm=70.0)
    # Independently brute-force the expected best gear (mirror the selection rule:
    # prefer combos in [50,110] rpm, pick the one closest to prefer_rpm).
    combos = []
    for c in bike.gearing["chainrings_t"]:
        for k in bike.gearing["cassette_t"]:
            r = cadence_rpm(12.0, c, k, bike.wheel_circ_m)
            combos.append((c, k, r))
    in_range = [t for t in combos if 50.0 <= t[2] <= 110.0]
    pool = in_range if in_range else combos
    exp_cr, exp_cog, exp_rpm = min(pool, key=lambda t: abs(t[2] - 70.0))
    assert (cr, cog) == (exp_cr, exp_cog)
    assert abs(rpm - exp_rpm) < 1e-9


def test_suggest_gear_none_without_gearing():
    assert suggest_gear(20.0, NoGearBike()) is None


def test_suggest_gear_fallback_when_no_in_range():
    # 2 km/h: cadence in every gear is far below 50 rpm -> fallback to all_combos.
    bike = FakeBike()
    result = suggest_gear(2.0, bike, prefer_rpm=70.0)
    assert result is not None
    cr, cog, rpm = result
    assert (cr, cog) in {(c, k) for c in bike.gearing["chainrings_t"]
                         for k in bike.gearing["cassette_t"]}
    assert rpm < 50.0  # confirms we're genuinely in the fallback regime
