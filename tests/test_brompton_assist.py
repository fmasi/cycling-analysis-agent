import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bike_config import load_bike
from physics_model import solve_speed_with_assist

BROMPTON = load_bike("brompton_g")


def test_l0_equals_self_power():
    # L0 = no assist; result should equal pure predict_speed wattage path
    from physics_model import predict_speed
    rider_w = 120
    r = solve_speed_with_assist(rider_w, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L0")
    v_self = predict_speed(rider_w, 0.0, bike=BROMPTON, surface="tarmac", system_weight_kg=98.5)
    assert abs(r.speed_kmh - v_self) < 0.01
    assert r.motor_w == 0


def test_l1_adds_motor_below_cutoff():
    # On a moderate climb at rider 120 W: L1 adds motor up to motor_max OR until cutoff.
    r = solve_speed_with_assist(rider_w=120, grade_pct=4.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L1")
    assert r.motor_w > 0
    assert r.speed_kmh <= 25.0  # below cutoff


def test_motor_drops_to_zero_above_cutoff():
    # Strong rider effort on flat → speed pushes above 25 km/h, motor disengages
    r = solve_speed_with_assist(rider_w=250, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L2")
    if r.speed_kmh > 25.0:
        assert r.motor_w == 0


def test_motor_w_capped_at_rated():
    # On steep climb at high rider effort with L3 multiplier 1.5, motor would scale beyond rated
    # but must cap at bike.assist.rated_w (250 W).
    r = solve_speed_with_assist(rider_w=200, grade_pct=8.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L3")
    assert r.motor_w <= 250


def test_battery_drain_wh_per_hour_field():
    # Output exposes Wh/hour drain for battery-range estimation
    r = solve_speed_with_assist(rider_w=100, grade_pct=2.0, bike=BROMPTON, surface="tarmac",
                                 system_weight_kg=98.5, assist_level="L1")
    assert hasattr(r, "wh_per_hour")
    assert r.wh_per_hour >= 0
    # Wh/hour ≈ motor_w because 1 W × 1 h = 1 Wh
    assert abs(r.wh_per_hour - r.motor_w) < 1.0
