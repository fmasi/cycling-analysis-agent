"""Tests for the physics solver and zone classifier."""
import physics_model as pm


def test_speed_power_roundtrip():
    # predict_speed must be the inverse of predict_power across grades.
    for g in [0, 3, 6, 9, -5]:
        for P in [120, 200, 300]:
            v = pm.predict_speed(P, g, system_weight_kg=90, cda=0.30, crr=0.005)
            P2 = pm.predict_power(v, g, system_weight_kg=90, cda=0.30, crr=0.005)
            assert abs(P2 - P) < 1.0, (g, P, v, P2)


def test_speed_increases_with_power_on_a_climb():
    lo = pm.predict_speed(150, 8, system_weight_kg=90)
    hi = pm.predict_speed(300, 8, system_weight_kg=90)
    assert hi > lo > 0


def test_descent_terminal_speed_is_high_but_cappable():
    uncapped = pm.predict_speed(0, -10, system_weight_kg=90)
    assert uncapped > 40                      # unbraked coasting is fast
    assert pm.predict_speed(0, -10, system_weight_kg=90, cap_kmh=60) == 60


def test_zone_overlap_resolves_to_highest_intensity(monkeypatch):
    # Synthetic overlapping ladder: Z3∩Z4 and Z4∩Z5 overlap by design.
    zones = [
        ("Z1", 0, 99),
        ("Z3", 100, 180),
        ("Z4", 170, 190),   # Sweet Spot overlay
        ("Z5", 184, 200),
        ("Z8", 201, 400),
    ]
    monkeypatch.setattr(pm, "ZONES", zones)
    assert pm.zone_for_power(50) == "Z1"
    assert pm.zone_for_power(175) == "Z4"     # in Z3∩Z4 → higher (Sweet Spot reachable)
    assert pm.zone_for_power(186) == "Z5"     # in Z4∩Z5 → higher
    assert pm.zone_for_power(120) == "Z3"
    assert pm.zone_for_power(500) == "Z8"     # above top → clamps to last
    assert pm.zone_for_power(-10) == "Z1"     # below bottom → clamps to first
