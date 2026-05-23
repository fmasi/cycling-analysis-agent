"""Hermetic tests for load_bike's assist-block mapping.

Unlike tests/test_brompton_assist.py (which loads the real, gitignored
USER_PROFILE.md), these pass a synthetic profile dict mirroring the EVOLVED
assist schema actually used in USER_PROFILE.md: cutoff comes from
`legal_cutoff_kph`, per-level multipliers live as `share` inside `levels`,
there is no top-level `boost_mode`/`level_share`, and `battery_range_km` is a
dict. load_bike must map this onto AssistConfig.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bike_config import load_bike

EVOLVED_PROFILE = {
    "default_bike": "brompton_g",
    "bikes": {
        "brompton_g": {
            "name": "Brompton G Line Electric",
            "bike_weight_kg": 19.5,
            "system_weight_kg_default": 98.5,
            "fr_split": "40/60",
            "cda": 0.42,
            "drivetrain_efficiency": 0.96,
            "wheel_circ_m": 1.59,
            "has_power_meter": False,
            "tyres": {},
            "crr_by_surface": {"tarmac": 0.0100, "gravel_smooth": 0.0180},
            "surfaces_supported": ["tarmac", "gravel"],
            "assist": {
                "type": "e-Motiq",
                "placement": "rear_hub",
                "rated_w": 250,
                "peak_w": None,
                "torque_nm": None,
                "sensor": "torque",
                "legal_cutoff_kph": 25,
                "legal_cutoff_kph_us": 32,
                "levels": {
                    "L0": {"name": "no assist", "share": 0.0},
                    "L1": {"name": "low", "share": 0.5},
                    "L2": {"name": "medium", "share": 1.0},
                    "L3": {"name": "high", "share": 1.5, "mode": "sustained"},
                },
                "battery_wh": 345,
                "battery_range_km": {"manual_typical": "30-70",
                                     "brompton_marketing": 90},
                "default_level_flat": "L1",
                "default_level_climb_5pct": "L2",
                "default_level_climb_10pct": "L3",
            },
        }
    },
}


def test_assist_parses_from_evolved_schema():
    bike = load_bike("brompton_g", profile=EVOLVED_PROFILE)
    assert bike.assist is not None


def test_cutoff_from_legal_cutoff_kph():
    bike = load_bike("brompton_g", profile=EVOLVED_PROFILE)
    assert bike.assist.cutoff_kph == 25


def test_level_share_derived_from_levels():
    bike = load_bike("brompton_g", profile=EVOLVED_PROFILE)
    assert bike.assist.level_share == {"L0": 0.0, "L1": 0.5, "L2": 1.0, "L3": 1.5}


def test_rated_and_battery_carried_through():
    bike = load_bike("brompton_g", profile=EVOLVED_PROFILE)
    assert bike.assist.rated_w == 250
    assert bike.assist.battery_wh == 345


def test_boost_mode_defaults_when_absent():
    bike = load_bike("brompton_g", profile=EVOLVED_PROFILE)
    assert bike.assist.boost_mode is False


def test_explicit_cutoff_and_level_share_still_honoured():
    """Backward-compat: a profile using the OLD schema (explicit cutoff_kph +
    level_share) must still work."""
    prof = {
        "default_bike": "b",
        "bikes": {
            "b": {
                "name": "Old Schema", "bike_weight_kg": 19.0,
                "system_weight_kg_default": 98.0, "fr_split": "40/60",
                "cda": 0.42, "drivetrain_efficiency": 0.96, "wheel_circ_m": 1.59,
                "has_power_meter": False, "tyres": {},
                "crr_by_surface": {"tarmac": 0.01}, "surfaces_supported": ["tarmac"],
                "assist": {
                    "type": "e", "placement": "rear_hub", "rated_w": 250,
                    "peak_w": None, "torque_nm": None, "sensor": "torque",
                    "cutoff_kph": 25, "levels": ["L0", "L1"], "boost_mode": True,
                    "battery_wh": 345, "battery_range_km": "30-70",
                    "level_share": {"L0": 0.0, "L1": 0.5},
                    "default_level_flat": "L1", "default_level_climb_5pct": "L1",
                    "default_level_climb_10pct": "L1",
                },
            }
        },
    }
    bike = load_bike("b", profile=prof)
    assert bike.assist.cutoff_kph == 25
    assert bike.assist.level_share == {"L0": 0.0, "L1": 0.5}
    assert bike.assist.boost_mode is True
