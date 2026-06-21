"""Tests for bike auto-detection + resolution."""
import pytest

from bike_cli import detect_bike_from_power, resolve_bike, resolve_surface
from bike_config import UnknownBikeError

# Two-bike profile: roadie has a power meter, ebike does not.
PROFILE = {
    "default_bike": "roadie",
    "bikes": {
        "roadie": {
            "name": "Roadie", "bike_weight_kg": 8.5, "system_weight_kg_default": 90.0,
            "fr_split": "45/55", "cda": 0.30, "drivetrain_efficiency": 0.97,
            "wheel_circ_m": 2.155, "has_power_meter": True, "tyres": {},
            "crr_by_surface": {"tarmac": 0.005}, "surfaces_supported": ["tarmac"],
        },
        "ebike": {
            "name": "E-Bike", "bike_weight_kg": 20.0, "system_weight_kg_default": 102.0,
            "fr_split": "40/60", "cda": 0.42, "drivetrain_efficiency": 0.96,
            "wheel_circ_m": 1.59, "has_power_meter": False, "tyres": {},
            "crr_by_surface": {"tarmac": 0.01, "gravel": 0.018},
            "surfaces_supported": ["tarmac", "gravel"],
        },
    },
}


def test_detect_from_power_presence():
    assert detect_bike_from_power(True, PROFILE) == "roadie"
    assert detect_bike_from_power(False, PROFILE) == "ebike"


def test_detect_ambiguous_returns_none():
    prof = {"bikes": {"a": {"has_power_meter": True}, "b": {"has_power_meter": True}}}
    assert detect_bike_from_power(True, prof) is None


def test_resolve_flag_wins():
    bike, source = resolve_bike("ebike", profile=PROFILE, fit_has_power=True)
    assert bike.slug == "ebike" and source == "flag"


def test_resolve_auto_from_fit_power():
    bike, source = resolve_bike(None, profile=PROFILE, fit_has_power=True)
    assert bike.slug == "roadie" and source == "power"
    bike, source = resolve_bike(None, profile=PROFILE, fit_has_power=False)
    assert bike.slug == "ebike" and source == "power"


def test_resolve_commute_filename():
    bike, source = resolve_bike(None, profile=PROFILE, gpx_path="routes/morning-commute.gpx")
    assert bike.slug == "ebike" and source == "filename"


def test_resolve_falls_back_to_default(capsys):
    bike, source = resolve_bike(None, profile=PROFILE, quiet=False)
    assert bike.slug == "roadie" and source == "default"
    assert "default bike" in capsys.readouterr().err


def test_resolve_unknown_slug_raises():
    with pytest.raises(UnknownBikeError):
        resolve_bike("penny", profile=PROFILE)


def test_resolve_surface_default_and_validation():
    bike, _ = resolve_bike("ebike", profile=PROFILE)
    assert resolve_surface(bike) == "tarmac"          # first supported
    assert resolve_surface(bike, "gravel") == "gravel"
    with pytest.raises(Exception):
        resolve_surface(bike, "snow")
