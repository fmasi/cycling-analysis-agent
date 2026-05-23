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
    cr, cog, rpm = suggest_gear(12.0, FakeBike(), prefer_rpm=70.0)
    assert (cr, cog) in {(c, k) for c in FakeBike.gearing["chainrings_t"]
                         for k in FakeBike.gearing["cassette_t"]}
    assert 60 <= rpm <= 80  # close to 70


def test_suggest_gear_none_without_gearing():
    assert suggest_gear(20.0, NoGearBike()) is None
