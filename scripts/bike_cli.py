"""Shared CLI helper: --bike / --surface / --assist-level argument resolution.

Resolution order:
1. --bike passed and matches bikes: dict → use it.
2. --bike passed but no match → hard fail with valid-slug list.
3. --bike omitted → use default_bike, emit a one-line stderr warning.

--surface defaults to the bike's first surfaces_supported.
--assist-level defaults to bike.assist.default_level_flat for assisted bikes,
None for unassisted (with --assist-level silently ignored when not applicable).
"""
from __future__ import annotations
import argparse
import sys
from typing import Optional, Tuple

from bike_config import BikeConfig, load_bike, UnknownBikeError, UnsupportedSurfaceError
from profile import load_profile


def add_bike_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bike",
        default=None,
        help="Bike slug from USER_PROFILE.md bikes: dict. Defaults to default_bike.",
    )
    parser.add_argument(
        "--surface",
        default=None,
        help="Surface key under the bike's crr_by_surface; defaults to first surfaces_supported.",
    )
    parser.add_argument(
        "--assist-level",
        default=None,
        choices=["L0", "L1", "L2", "L3"],
        help="Assist level for motorised bikes (ignored otherwise). Defaults to bike's default_level_flat.",
    )


def resolve_bike(args: argparse.Namespace) -> Tuple[BikeConfig, str, Optional[str]]:
    profile = load_profile()
    if args.bike is None:
        slug = profile.get("default_bike")
        print(f"using default bike '{slug}' (no --bike specified)", file=sys.stderr)
    else:
        slug = args.bike
    bike = load_bike(slug=slug, profile=profile)

    surface = args.surface or bike.surfaces_supported[0]
    bike.validate_surface(surface)

    level: Optional[str] = None
    if bike.assist is not None:
        level = args.assist_level or bike.assist.default_level_flat
    return bike, surface, level
