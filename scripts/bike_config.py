"""BikeConfig / AssistConfig dataclasses and the load_bike() helper.

Reads the `bikes:` dict from USER_PROFILE.md (via profile.load_profile) and
returns a typed config object. Single source of truth for per-bike physics —
weight, F/R split, CdA, drivetrain, tyres/CRR-by-surface, gearing, and the
e-assist model for motorised bikes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from profile import load_profile, parse_fr_split


class UnknownBikeError(ValueError):
    pass


class UnsupportedSurfaceError(ValueError):
    pass


@dataclass
class AssistConfig:
    type: str
    placement: str
    rated_w: int
    peak_w: Optional[int]
    torque_nm: Optional[int]
    sensor: str
    cutoff_kph: float
    levels: list[str]
    boost_mode: bool
    battery_wh: int
    battery_range_km: str | dict | None
    level_share: dict[str, float]
    default_level_flat: str
    default_level_climb_5pct: str
    default_level_climb_10pct: str


@dataclass
class BikeConfig:
    slug: str
    name: str
    bike_weight_kg: float
    system_weight_kg_default: float
    fr_split: str
    cda: float
    cda_range: str
    drivetrain_efficiency: float
    wheel_circ_m: float
    has_power_meter: bool
    tyres: dict
    crr_by_surface: dict[str, float]
    surfaces_supported: list[str]
    assist: Optional[AssistConfig] = None
    tyre_pressure_psi: Optional[dict] = None
    tyre_pressure_uncertainty_psi: Optional[float] = None
    unvalidated_by_model: bool = False
    unvalidated_by_model_source: Optional[str] = None
    gearing: Optional[dict] = None

    @property
    def fr_split_front_pct(self) -> float:
        """Front-wheel weight share as a percent (e.g. "40/60" -> 40.0)."""
        front = parse_fr_split(self.fr_split)
        return front if front is not None else 48.0

    def validate_surface(self, surface: str) -> None:
        if surface in self.crr_by_surface:
            return
        raise UnsupportedSurfaceError(
            f"Surface '{surface}' not supported by bike '{self.slug}'. "
            f"Supported surfaces: {list(self.crr_by_surface)}"
        )


def _parse_assist(slug: str, a: dict) -> Optional[AssistConfig]:
    """Map a raw `assist:` block onto AssistConfig, tolerating schema drift.

    Assist is optional and unused by physics that doesn't need it (e.g. tyre
    pressure), so a malformed block degrades to None with a stderr warning
    rather than blocking the whole bike load.
    """
    import sys

    try:
        # cutoff: explicit `cutoff_kph` (old schema) else the EU legal cutoff.
        cutoff = a.get("cutoff_kph")
        if cutoff is None:
            cutoff = a["legal_cutoff_kph"]
        # level_share: explicit top-level dict (old schema) else derive each
        # level's multiplier from levels.<L>.share (current schema, where
        # `levels` is a per-mode dict).
        level_share = a.get("level_share")
        if level_share is None:
            levels_block = a.get("levels")
            if not isinstance(levels_block, dict):
                raise KeyError("level_share")
            level_share = {
                lvl: d["share"]
                for lvl, d in levels_block.items()
                if isinstance(d, dict) and d.get("share") is not None
            }
        # `levels` as a name list works for a dict (-> keys) or a plain list.
        levels_names = list(a.get("levels", []))
        return AssistConfig(
            type=a["type"],
            placement=a["placement"],
            rated_w=int(a["rated_w"]),
            peak_w=int(a["peak_w"]) if a.get("peak_w") is not None else None,
            torque_nm=int(a["torque_nm"]) if a.get("torque_nm") is not None else None,
            sensor=a["sensor"],
            cutoff_kph=float(cutoff),
            levels=levels_names,
            boost_mode=bool(a.get("boost_mode", False)),
            battery_wh=int(a["battery_wh"]),
            battery_range_km=a.get("battery_range_km"),
            level_share={k: float(v) for k, v in level_share.items()},
            default_level_flat=a["default_level_flat"],
            default_level_climb_5pct=a["default_level_climb_5pct"],
            default_level_climb_10pct=a["default_level_climb_10pct"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        print(
            f"⚠ assist block for bike '{slug}' did not parse "
            f"({type(exc).__name__}: {exc}); continuing with assist=None.",
            file=sys.stderr,
        )
        return None


def load_bike(slug: Optional[str] = None, *, profile: Optional[dict] = None) -> BikeConfig:
    """Return the typed BikeConfig for `slug` (or the profile's default_bike).

    Pass `profile` to load from an in-memory dict (tests); omit it to read the
    real profile via load_profile().
    """
    if profile is None:
        profile = load_profile()
    bikes = profile.get("bikes") or {}
    if not bikes:
        raise UnknownBikeError("No bikes: block in USER_PROFILE.md")
    if slug is None:
        slug = profile.get("default_bike")
        if slug is None:
            raise UnknownBikeError("default_bike: not set in USER_PROFILE.md")
    if slug not in bikes:
        raise UnknownBikeError(
            f"Unknown bike slug: '{slug}'. Valid slugs: {sorted(bikes)}"
        )
    raw = bikes[slug]
    assist = _parse_assist(slug, raw["assist"]) if isinstance(raw.get("assist"), dict) else None
    tp_uncertainty = raw.get("tyre_pressure_uncertainty_psi")
    return BikeConfig(
        slug=slug,
        name=raw["name"],
        bike_weight_kg=float(raw["bike_weight_kg"]),
        system_weight_kg_default=float(raw["system_weight_kg_default"]),
        fr_split=str(raw["fr_split"]),
        cda=float(raw["cda"]),
        cda_range=raw.get("cda_range", ""),
        drivetrain_efficiency=float(raw["drivetrain_efficiency"]),
        wheel_circ_m=float(raw["wheel_circ_m"]),
        has_power_meter=bool(raw["has_power_meter"]),
        tyres=raw.get("tyres") or {},
        crr_by_surface={k: float(v) for k, v in raw["crr_by_surface"].items()},
        surfaces_supported=list(raw["surfaces_supported"]),
        assist=assist,
        tyre_pressure_psi=raw.get("tyre_pressure_psi") or None,
        tyre_pressure_uncertainty_psi=float(tp_uncertainty) if tp_uncertainty is not None else None,
        unvalidated_by_model=bool(raw.get("unvalidated_by_model", False)),
        unvalidated_by_model_source=raw.get("unvalidated_by_model_source"),
        gearing=raw.get("gearing") or None,
    )


def list_bikes(*, profile: Optional[dict] = None) -> list[str]:
    """Return registered bike slugs from the profile's `bikes:` block."""
    if profile is None:
        profile = load_profile()
    return sorted((profile.get("bikes") or {}).keys())


def default_bike_slug(*, profile: Optional[dict] = None) -> Optional[str]:
    """Return the profile's `default_bike` slug (or None)."""
    if profile is None:
        profile = load_profile()
    return profile.get("default_bike")
