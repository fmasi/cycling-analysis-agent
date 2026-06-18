"""Tests for bike_config.load_bike — typed per-bike config.

Hermetic: every test passes a synthetic profile dict (via the `synthetic_profile`
fixture or an inline literal), never the real USER_PROFILE.md.
"""
import pytest

from bike_config import (
    BikeConfig,
    load_bike,
    list_bikes,
    default_bike_slug,
    UnknownBikeError,
    UnsupportedSurfaceError,
)


def test_load_default_bike(synthetic_profile):
    bike = load_bike(slug=None, profile=synthetic_profile)
    assert bike.slug == "roadie"
    assert bike.name == "Test Roadie"
    assert bike.bike_weight_kg == 8.5
    assert bike.wheel_circ_m == 2.105
    assert bike.fr_split_front_pct == 45.0
    assert bike.crr_by_surface["tarmac"] == 0.0050
    assert bike.surfaces_supported == ["tarmac"]
    assert bike.assist is None
    assert bike.gearing["chainrings_t"] == [34, 50]


def test_load_explicit_bike(synthetic_profile):
    bike = load_bike(slug="ebike", profile=synthetic_profile)
    assert bike.slug == "ebike"
    assert bike.bike_weight_kg == 20.0
    assert bike.wheel_circ_m == 1.59
    assert bike.crr_by_surface["gravel_smooth"] == 0.0180
    assert bike.gearing is None
    assert bike.assist is not None


def test_unknown_slug_raises_with_valid_list(synthetic_profile):
    with pytest.raises(UnknownBikeError) as exc:
        load_bike(slug="penny_farthing", profile=synthetic_profile)
    msg = str(exc.value)
    assert "penny_farthing" in msg
    assert "roadie" in msg and "ebike" in msg


def test_no_bikes_block_raises():
    with pytest.raises(UnknownBikeError):
        load_bike(slug=None, profile={"default_bike": "x", "bikes": {}})


def test_surface_validation_supported(synthetic_profile):
    load_bike(slug="ebike", profile=synthetic_profile).validate_surface("gravel_smooth")


def test_surface_validation_unsupported(synthetic_profile):
    bike = load_bike(slug="roadie", profile=synthetic_profile)
    with pytest.raises(UnsupportedSurfaceError) as exc:
        bike.validate_surface("gravel_rough")
    assert "gravel_rough" in str(exc.value) and "tarmac" in str(exc.value)


def test_surface_validation_rejects_suffix_variants(synthetic_profile):
    # exact-match, not a startswith fuzzy match
    bike = load_bike(slug="roadie", profile=synthetic_profile)
    with pytest.raises(UnsupportedSurfaceError):
        bike.validate_surface("tarmac_garbage")


def test_list_bikes_and_default(synthetic_profile):
    assert list_bikes(profile=synthetic_profile) == ["ebike", "roadie"]
    assert default_bike_slug(profile=synthetic_profile) == "roadie"


# --- assist-block mapping (evolved + old schema) ----------------------------

def test_assist_parses_evolved_schema(synthetic_profile):
    bike = load_bike(slug="ebike", profile=synthetic_profile)
    a = bike.assist
    assert a.cutoff_kph == 25                      # from legal_cutoff_kph
    assert a.level_share == {"L0": 0.0, "L1": 0.5, "L2": 1.0}  # from levels.<L>.share
    assert a.rated_w == 250
    assert a.battery_wh == 345
    assert a.boost_mode is False                   # defaults when absent
    assert a.default_level_flat == "L1"


def test_assist_old_schema_still_supported():
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
    a = load_bike("b", profile=prof).assist
    assert a.cutoff_kph == 25
    assert a.level_share == {"L0": 0.0, "L1": 0.5}
    assert a.boost_mode is True


def test_malformed_assist_degrades_to_none(capsys):
    prof = {
        "default_bike": "b",
        "bikes": {
            "b": {
                "name": "Bad Assist", "bike_weight_kg": 19.0,
                "system_weight_kg_default": 98.0, "fr_split": "40/60",
                "cda": 0.42, "drivetrain_efficiency": 0.96, "wheel_circ_m": 1.59,
                "has_power_meter": False, "tyres": {},
                "crr_by_surface": {"tarmac": 0.01}, "surfaces_supported": ["tarmac"],
                "assist": {"type": "e"},  # missing required keys
            }
        },
    }
    bike = load_bike("b", profile=prof)
    assert bike.assist is None                      # degraded, not crashed
    assert "did not parse" in capsys.readouterr().err
