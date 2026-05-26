import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from physics_model import predict_speed, predict_power, speed_at_cadence_rpm, vam_at_power, power_for_60rpm_in_lowest_gear
from bike_config import load_bike

TRIPSTER = load_bike("tripster")
BROMPTON = load_bike("brompton_g")


def test_predict_speed_tripster_flat_at_ftp():
    # 171 W FTP, 0% grade, system 90.1 kg, tarmac (CdA 0.28, CRR 0.005, eta 0.97)
    # Physics: 171 × 0.97 = 165.9 W at wheel → ~32.5 km/h on flat
    v = predict_speed(power_crank_w=171, grade_pct=0.0, bike=TRIPSTER, surface="tarmac",
                      system_weight_kg=90.1)
    assert 30.0 < v < 35.0, f"expected ~32.5 km/h, got {v:.2f}"


def test_predict_speed_brompton_flat_at_120w_tarmac():
    # Brompton at 120 W rider crank, 0% grade, system 98.5 kg, tarmac (CRR 0.010)
    # Higher CdA, higher CRR, heavier bike → should be much slower than Tripster at same wattage
    v_tripster = predict_speed(power_crank_w=120, grade_pct=0.0, bike=TRIPSTER, surface="tarmac",
                               system_weight_kg=90.1)
    v_brompton = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                               system_weight_kg=98.5)
    assert v_brompton < v_tripster - 4.0, f"Brompton should be >=4 km/h slower; got {v_brompton:.1f} vs {v_tripster:.1f}"


def test_predict_speed_brompton_gravel_slower_than_tarmac():
    v_tarmac = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="tarmac",
                             system_weight_kg=98.5)
    v_gravel = predict_speed(power_crank_w=120, grade_pct=0.0, bike=BROMPTON, surface="gravel_smooth",
                             system_weight_kg=98.5)
    assert v_gravel < v_tarmac, f"gravel must be slower than tarmac; got {v_gravel:.1f} vs {v_tarmac:.1f}"


def test_predict_speed_uses_bike_drivetrain_efficiency():
    # Brompton eta=0.96 vs Tripster eta=0.97 at otherwise equal physics
    v_a = predict_speed(power_crank_w=150, grade_pct=2.0, bike=TRIPSTER, surface="tarmac",
                        system_weight_kg=90.1)
    v_b = predict_speed(power_crank_w=150, grade_pct=2.0, bike=BROMPTON, surface="tarmac",
                        system_weight_kg=90.1)
    assert v_a != v_b


def test_predict_power_brompton_climb():
    # Approx 18 km/h at 5% on Brompton → check it's a sensible wattage band
    w = predict_power(speed_kmh=18.0, grade_pct=5.0, bike=BROMPTON, surface="tarmac",
                       system_weight_kg=98.5)
    assert 240 < w < 400, f"expected ~240–400 W, got {w:.1f}"


def test_speed_at_cadence_brompton_wheel_circ():
    # Brompton wheel circ = 1.59 m, not the Tripster's 2.155 m.
    # 80 rpm × gear ratio 50/15 → wheel rpm 80 * (50/15) = 266.7 rpm = 4.44 rps × 1.59 m = 7.06 m/s = 25.4 km/h
    v = speed_at_cadence_rpm(cadence_rpm=80, gear_ratio=50/15, wheel_circ_m=BROMPTON.wheel_circ_m)
    assert 24.5 < v < 26.5, f"expected ~25.4 km/h, got {v:.2f}"


def test_vam_at_power_uses_bike():
    vam = vam_at_power(power_crank_w=171, grade_pct=8.0, bike=TRIPSTER, surface="tarmac",
                        system_weight_kg=90.1)
    assert 600 < vam < 1000


def test_power_for_60rpm_lowest_gear_brompton():
    # Brompton lowest gear: 50T chainring × 18T cog = ratio 2.78
    # speed_at_cadence_rpm(60, 2.78, 1.59m) = 60 × 2.78 × 1.59 × 60/1000 = 15.9 km/h
    # (Note: the plan spec comment incorrectly inverted the ratio; the function formula is correct.)
    # At 15.9 km/h on 10% grade, Brompton (CdA 0.42, CRR 0.010, eta 0.96, 98.5 kg):
    #   gravity+rolling ≈ (sin(atan(0.1)) + 0.01) × 98.5 × 9.81 × 4.42 ≈ 467 W at wheel
    #   aero ≈ 0.5 × 1.225 × 0.42 × 4.42² × 4.42 ≈ 22 W at wheel
    #   p_crank ≈ 489/0.96 ≈ 510 W
    w = power_for_60rpm_in_lowest_gear(grade_pct=10.0, lowest_ratio=50/18, bike=BROMPTON,
                                          surface="tarmac", system_weight_kg=98.5)
    assert 450 < w < 570, f"unexpected wattage for 60rpm-lowest-Brompton-10pct: {w:.1f}"
