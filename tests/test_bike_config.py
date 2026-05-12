from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from bike_config import BikeConfig, AssistConfig, load_bike, UnknownBikeError, UnsupportedSurfaceError


def test_load_tripster_default():
    bike = load_bike(slug=None)
    assert bike.slug == "tripster"
    assert bike.name == "Kinesis Decade Tripster"
    assert bike.bike_weight_kg == 11.6
    assert bike.wheel_circ_m == 2.155
    assert bike.crr_by_surface["tarmac"] == 0.0050
    assert bike.surfaces_supported == ["tarmac"]
    assert bike.assist is None


def test_load_brompton_explicit():
    bike = load_bike(slug="brompton_g")
    assert bike.slug == "brompton_g"
    assert bike.bike_weight_kg == 19.5
    assert bike.wheel_circ_m == 1.59
    assert bike.crr_by_surface["gravel_smooth"] == 0.0180
    assert bike.assist is not None
    assert bike.assist.cutoff_kph == 25
    assert bike.assist.level_share["L1"] == 0.5
    assert bike.assist.battery_wh == 345


def test_unknown_slug_raises_with_valid_list():
    try:
        load_bike(slug="penny_farthing")
    except UnknownBikeError as e:
        assert "penny_farthing" in str(e)
        assert "tripster" in str(e)
        assert "brompton_g" in str(e)
    else:
        raise AssertionError("expected UnknownBikeError")


def test_surface_validation_supported():
    bike = load_bike(slug="brompton_g")
    bike.validate_surface("gravel_smooth")  # no raise


def test_surface_validation_unsupported():
    bike = load_bike(slug="tripster")
    try:
        bike.validate_surface("gravel_rough")
    except UnsupportedSurfaceError as e:
        assert "gravel_rough" in str(e)
        assert "tarmac" in str(e)
    else:
        raise AssertionError("expected UnsupportedSurfaceError")


def test_surface_validation_rejects_suffix_variants():
    """A surface like 'tarmac_garbage' must NOT pass validation just because 'tarmac' is supported.

    The check is exact-match against crr_by_surface, not a startswith fuzzy match.
    """
    bike = load_bike(slug="tripster")
    try:
        bike.validate_surface("tarmac_garbage")
    except UnsupportedSurfaceError:
        pass
    else:
        raise AssertionError("expected UnsupportedSurfaceError for unknown surface variant")
