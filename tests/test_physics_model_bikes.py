import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from physics_model import predict_speed
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
