"""Shared --bike / --surface CLI resolution with auto-detection.

Bike selection priority (highest first):
  1. explicit ``--bike <slug>``
  2. auto-detect from signals — FIT power presence (power meter ⇒ the bike whose
     ``has_power_meter`` matches) or a GPX filename containing "commute"
  3. the profile's ``default_bike`` (with a one-line stderr note)

Surface defaults to the bike's first ``surfaces_supported`` entry and is
validated against the bike's ``crr_by_surface``.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Tuple

from bike_config import BikeConfig, load_bike, list_bikes, UnknownBikeError  # noqa: F401
from profile import load_profile


def add_bike_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bike", default=None,
        help="Bike slug from the profile's bikes: registry. "
             "Default: auto-detect (FIT power / GPX name) then default_bike.",
    )
    parser.add_argument(
        "--surface", default=None,
        help="Surface key under the bike's crr_by_surface; "
             "defaults to the bike's first surfaces_supported.",
    )


def detect_bike_from_power(has_power: bool, profile: dict) -> Optional[str]:
    """Slug whose has_power_meter matches the FIT's power presence.

    Returns None when the match is ambiguous (0 or >1 candidate bikes), so the
    caller can fall back to default_bike rather than guess.
    """
    bikes = profile.get("bikes") or {}
    matches = [
        slug for slug, b in bikes.items()
        if bool(b.get("has_power_meter")) == bool(has_power)
    ]
    return matches[0] if len(matches) == 1 else None


def resolve_bike(
    bike_arg: Optional[str] = None,
    *,
    profile: Optional[dict] = None,
    fit_has_power: Optional[bool] = None,
    gpx_path: Optional[str] = None,
    quiet: bool = False,
) -> Tuple[BikeConfig, str]:
    """Resolve a BikeConfig + the selection source ('flag'|'power'|'filename'|'default').

    Raises UnknownBikeError if an explicit --bike slug is unknown.
    """
    if profile is None:
        profile = load_profile()

    slug: Optional[str] = bike_arg
    source = "flag"
    if slug is None and fit_has_power is not None:
        slug = detect_bike_from_power(fit_has_power, profile)
        source = "power"
    if slug is None and gpx_path and "commute" in str(gpx_path).lower():
        # A commute route ⇒ the non-power bike, if that's unambiguous.
        slug = detect_bike_from_power(False, profile)
        source = "filename"
    if slug is None:
        slug = profile.get("default_bike")
        source = "default"
        if not quiet:
            print(f"[bike] no signal — using default bike '{slug}'", file=sys.stderr)

    bike = load_bike(slug, profile=profile)
    return bike, source


def resolve_surface(bike: BikeConfig, surface_arg: Optional[str] = None) -> Optional[str]:
    """Return the surface key to use (validated), defaulting to the bike's first."""
    surface = surface_arg
    if surface is None and bike.surfaces_supported:
        surface = bike.surfaces_supported[0]
    if surface is not None:
        bike.validate_surface(surface)
    return surface
