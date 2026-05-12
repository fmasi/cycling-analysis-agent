import argparse
import sys
from io import StringIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from bike_cli import add_bike_args, resolve_bike
from bike_config import UnknownBikeError, UnsupportedSurfaceError


def _parser():
    p = argparse.ArgumentParser()
    add_bike_args(p)
    return p


def test_explicit_brompton_gravel():
    args = _parser().parse_args(["--bike", "brompton_g", "--surface", "gravel_smooth"])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "brompton_g"
    assert surface == "gravel_smooth"
    assert level == "L1"  # default_level_flat for Brompton


def test_default_bike_warns(capsys):
    args = _parser().parse_args([])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "tripster"
    assert surface == "tarmac"  # first of surfaces_supported
    assert level is None
    captured = capsys.readouterr()
    assert "using default bike 'tripster'" in captured.err


def test_bad_slug_hard_fails():
    args = _parser().parse_args(["--bike", "penny_farthing"])
    try:
        resolve_bike(args)
    except UnknownBikeError as e:
        assert "tripster" in str(e)
        assert "brompton_g" in str(e)
    else:
        raise AssertionError("expected UnknownBikeError")


def test_surface_not_supported_fails():
    args = _parser().parse_args(["--bike", "tripster", "--surface", "gravel_smooth"])
    try:
        resolve_bike(args)
    except UnsupportedSurfaceError:
        pass
    else:
        raise AssertionError("expected UnsupportedSurfaceError")


def test_assist_level_ignored_for_unassisted():
    args = _parser().parse_args(["--bike", "tripster", "--assist-level", "L2"])
    bike, surface, level = resolve_bike(args)
    assert bike.slug == "tripster"
    assert level is None  # assist-level silently ignored for unassisted bikes
