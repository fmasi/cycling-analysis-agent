import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bike_config import load_bike

PROFILE = {
    "default_bike": "tripster",
    "bikes": {
        "tripster": {
            "name": "Test Tripster",
            "bike_weight_kg": 11.6,
            "system_weight_kg_default": 90.1,
            "fr_split": "40/60",
            "cda": 0.28,
            "cda_range": "0.26–0.30",
            "drivetrain_efficiency": 0.97,
            "wheel_circ_m": 2.155,
            "has_power_meter": True,
            "tyres": {},
            "crr_by_surface": {"tarmac": 0.005},
            "surfaces_supported": ["tarmac"],
            "gearing": {
                "chainrings_t": [30, 39, 50],
                "cassette_t": [11, 12, 13, 14, 15, 17, 19, 21, 24, 28, 32],
            },
        },
        "nogears": {
            "name": "No Gears",
            "bike_weight_kg": 20.0,
            "system_weight_kg_default": 98.0,
            "fr_split": "40/60",
            "cda": 0.42,
            "cda_range": "0.40–0.45",
            "drivetrain_efficiency": 0.96,
            "wheel_circ_m": 1.59,
            "has_power_meter": False,
            "tyres": {},
            "crr_by_surface": {"tarmac": 0.01},
            "surfaces_supported": ["tarmac"],
        },
    },
}


def test_gearing_parsed_when_present():
    bike = load_bike("tripster", profile=PROFILE)
    assert bike.gearing["chainrings_t"] == [30, 39, 50]
    assert bike.gearing["cassette_t"][0] == 11
    assert bike.gearing["cassette_t"][-1] == 32


def test_gearing_none_when_absent():
    bike = load_bike("nogears", profile=PROFILE)
    assert bike.gearing is None
